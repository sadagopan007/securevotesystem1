from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import random
import hashlib
import time
import os
import csv

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "securevote-fixed-key-2024-xk9z")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = False

# ── LOAD VOTER DATABASE FROM CSV ──────────────────────────────────────
def load_voters():
    voters = {}
    try:
        with open("voters.csv", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                voters[row["voter_id"].strip().upper()] = row["aadhaar"].strip()
        print(f"✅ Loaded {len(voters)} voters from voters.csv")
    except FileNotFoundError:
        print("❌ voters.csv not found!")
    return voters

VOTER_DATABASE = load_voters()

# ── IN-MEMORY STORAGE ─────────────────────────────────────────────────
otp_storage    = {}
votes          = {}
fraud_log      = []
login_attempts = {}
trust_score    = [100]
voting_ended   = [False]   # ← NEW: controls result visibility

CANDIDATES = [
    {"id": "A", "name": "Arun Kumar",   "party": "Progressive Alliance", "symbol": "🌟"},
    {"id": "B", "name": "Bhavna Mehta", "party": "United Front",         "symbol": "🔥"},
    {"id": "C", "name": "Chetan Rao",   "party": "People's Party",       "symbol": "🌿"},
]

# ── HELPERS ───────────────────────────────────────────────────────────
def generate_vote_hash(voter_id, candidate, timestamp):
    data = f"{voter_id}{candidate}{timestamp}"
    return hashlib.sha256(data.encode()).hexdigest()[:16].upper()

def reduce_trust(amount=10):
    trust_score[0] = max(0, trust_score[0] - amount)

def get_results():
    counts = {c["id"]: 0 for c in CANDIDATES}
    for v in votes.values():
        counts[v["candidate"]] += 1
    return counts

# ── ROUTES ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login")
def login():
    return render_template("login.html", voting_ended=voting_ended[0])

@app.route("/end_voting", methods=["POST"])
def end_voting():
    voting_ended[0] = True
    return redirect(url_for("admin"))

@app.route("/send_otp", methods=["POST"])
def send_otp():
    if voting_ended[0]:
        return render_template("login.html", voting_ended=True,
            error="⚠ Voting has ended. No more votes accepted.")

    voter_id = request.form.get("voter_id", "").strip().upper()
    aadhaar  = request.form.get("aadhaar", "").strip()

    if not voter_id or not aadhaar:
        return render_template("login.html", voting_ended=False, error="Please fill all fields.")

    if len(aadhaar) != 12 or not aadhaar.isdigit():
        return render_template("login.html", voting_ended=False, error="Aadhaar must be 12 digits.")

    if voter_id not in VOTER_DATABASE:
        fraud_log.append({"type": "unregistered_voter", "voter_id": voter_id, "time": time.time()})
        reduce_trust(10)
        return render_template("login.html", voting_ended=False,
            error="⚠ Voter ID not found in database. This attempt has been flagged.", alert=True)

    if VOTER_DATABASE[voter_id] != aadhaar:
        fraud_log.append({"type": "aadhaar_mismatch", "voter_id": voter_id, "time": time.time()})
        reduce_trust(10)
        return render_template("login.html", voting_ended=False,
            error="⚠ Aadhaar does not match records. This attempt has been flagged.", alert=True)

    if voter_id in votes:
        fraud_log.append({"type": "double_vote_attempt", "voter_id": voter_id, "time": time.time()})
        reduce_trust(10)
        return render_template("login.html", voting_ended=False,
            error="⚠ This Voter ID has already voted. Attempt flagged.", alert=True)

    attempts = login_attempts.get(voter_id, 0)
    if attempts >= 5:
        fraud_log.append({"type": "brute_force", "voter_id": voter_id, "time": time.time()})
        reduce_trust(15)
        return render_template("login.html", voting_ended=False,
            error="⚠ Too many attempts. Contact election office.", alert=True)

    otp = random.randint(100000, 999999)
    otp_storage[voter_id] = {
        "otp":        otp,
        "aadhaar":    aadhaar,
        "expires_at": time.time() + 300
    }
    login_attempts[voter_id] = attempts + 1

    print(f"\n{'='*40}\n  OTP for {voter_id}: {otp}\n{'='*40}\n")
    return render_template("otp.html", voter_id=voter_id, otp_demo=otp)

@app.route("/verify_otp", methods=["POST"])
def verify_otp():
    voter_id    = request.form.get("voter_id", "").strip().upper()
    entered_otp = request.form.get("otp", "").strip()

    record = otp_storage.get(voter_id)
    if not record:
        return render_template("login.html", voting_ended=voting_ended[0],
                               error="Session expired. Please login again.")

    if time.time() > record["expires_at"]:
        del otp_storage[voter_id]
        return render_template("login.html", voting_ended=voting_ended[0],
                               error="OTP expired. Please login again.")

    if str(record["otp"]) != entered_otp:
        fraud_log.append({"type": "wrong_otp", "voter_id": voter_id, "time": time.time()})
        reduce_trust(5)
        return render_template("otp.html", voter_id=voter_id,
                               error="Wrong OTP. Try again.", otp_demo=record["otp"])

    session.clear()
    session["voter_id"]      = voter_id
    session["authenticated"] = True
    session.modified         = True
    return redirect(url_for("vote"))

@app.route("/vote")
def vote():
    voter_id = session.get("voter_id")
    auth     = session.get("authenticated")

    if not voter_id or not auth:
        return redirect(url_for("login"))

    if voter_id in votes:
        return redirect(url_for("success"))

    return render_template("vote.html", voter_id=voter_id, candidates=CANDIDATES)

@app.route("/cast_vote", methods=["POST"])
def cast_vote():
    voter_id = session.get("voter_id")
    auth     = session.get("authenticated")

    if not voter_id or not auth:
        return redirect(url_for("login"))

    candidate = request.form.get("candidate")

    if voter_id in votes:
        fraud_log.append({"type": "double_vote", "voter_id": voter_id, "time": time.time()})
        reduce_trust(10)
        return render_template("vote.html", voter_id=voter_id, candidates=CANDIDATES,
                               error="Fraud detected! You already voted.")

    if candidate not in [c["id"] for c in CANDIDATES]:
        return render_template("vote.html", voter_id=voter_id, candidates=CANDIDATES,
                               error="Invalid candidate selected.")

    timestamp = time.time()
    vote_hash = generate_vote_hash(voter_id, candidate, timestamp)
    votes[voter_id] = {"candidate": candidate, "timestamp": timestamp, "hash": vote_hash}

    session["vote_hash"]     = vote_hash
    session["voted_for"]     = candidate
    session["authenticated"] = False
    session.modified         = True
    return redirect(url_for("success"))

@app.route("/success")
def success():
    candidate_id = session.get("voted_for")
    vote_hash    = session.get("vote_hash", "N/A")

    if not candidate_id:
        return redirect(url_for("login"))

    candidate = next((c for c in CANDIDATES if c["id"] == candidate_id), None)
    return render_template("success.html", vote_hash=vote_hash, candidate=candidate)

@app.route("/admin")
def admin():
    results     = get_results()
    total_votes = len(votes)
    results_with_names = [
        {**c, "votes": results[c["id"]],
         "pct": round(results[c["id"]] / total_votes * 100) if total_votes else 0}
        for c in CANDIDATES
    ]
    return render_template("admin.html",
                           candidates=results_with_names,
                           total_votes=total_votes,
                           trust_score=trust_score[0],
                           fraud_log=fraud_log[-10:],
                           votes=votes,
                           voting_ended=voting_ended[0])

@app.route("/api/results")
def api_results():
    return jsonify({
        "results":      get_results(),
        "total":        len(votes),
        "trust_score":  trust_score[0],
        "fraud_events": len(fraud_log),
        "voting_ended": voting_ended[0]
    })

@app.route("/reset")
def reset():
    global votes, otp_storage, fraud_log, login_attempts
    votes          = {}
    otp_storage    = {}
    fraud_log      = []
    login_attempts = {}
    trust_score[0] = 100
    voting_ended[0] = False
    session.clear()
    print("✅ All data reset successfully!")
    return redirect(url_for("admin"))

@app.route("/database")
def database():
    return jsonify(VOTER_DATABASE)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
