
import os
import sqlite3
from flask import Flask, request, redirect, session, jsonify, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

DB = os.environ.get("DB_PATH", "messages.db")


def connect_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def setup():
    with connect_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                receiver TEXT NOT NULL,
                message TEXT NOT NULL,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


setup()


LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
<title>ConnectX</title>
<style>
body {
    background:#101522; color:white;
    font-family:Arial; display:flex;
    justify-content:center; align-items:center;
    height:100vh;
}
.box {
    background:#1c2638; padding:35px;
    border-radius:15px; width:300px;
    text-align:center;
}
input,button {
    box-sizing:border-box; width:100%;
    padding:13px; margin:9px 0;
    border:0; border-radius:7px;
}
button {background:#6755e8;color:white;cursor:pointer;}
a {color:#9d91ff;}
</style>
</head>
<body>
<div class="box">
<h1>ConnectX</h1>
<p>Connect with everyone.</p>
<h3>{{ heading }}</h3>
<form method="POST">
<input name="username" placeholder="Username" required>
<input name="password" type="password"
placeholder="Password (8+ characters)" required>
<button type="submit">{{ heading }}</button>
</form>
<p style="color:#ff8888">{{ error }}</p>
{% if register %}
<p>Already registered? <a href="/login">Login</a></p>
{% else %}
<p>New user? <a href="/register">Create account</a></p>
{% endif %}
</div>
</body>
</html>
"""


@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    return render_template_string(CHAT_PAGE, username=session["user"])


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        if not username.isalnum() or len(username) < 3:
            error = "Username must be at least 3 letters or numbers."
        elif len(password) < 8:
            error = "Password must have at least 8 characters."
        else:
            try:
                with connect_db() as con:
                    con.execute(
                        "INSERT INTO users VALUES (?, ?)",
                        (username, generate_password_hash(password))
                    )
                session["user"] = username
                return redirect("/")
            except sqlite3.IntegrityError:
                error = "Username already exists."

    return render_template_string(
        LOGIN_PAGE, heading="Register", error=error, register=True
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        with connect_db() as con:
            user = con.execute(
                "SELECT * FROM users WHERE username=?",
                (username,)
            ).fetchone()

        if user and check_password_hash(user["password"], password):
            session["user"] = username
            return redirect("/")
        error = "Incorrect username or password."

    return render_template_string(
        LOGIN_PAGE, heading="Login", error=error, register=False
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    if "user" not in session:
        return jsonify({"error": "Login required"}), 401

    me = session["user"]

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        receiver = str(data.get("receiver", "")).strip().lower()
        message = str(data.get("message", "")).strip()

        if not receiver or not message or len(message) > 2000:
            return jsonify({"error": "Invalid message"}), 400

        with connect_db() as con:
            exists = con.execute(
                "SELECT username FROM users WHERE username=?",
                (receiver,)
            ).fetchone()

            if not exists:
                return jsonify({"error": "User not found"}), 404

            con.execute(
                "INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)",
                (me, receiver, message)
            )

        return jsonify({"status": "sent"})

    receiver = request.args.get("with", "").strip().lower()

    with connect_db() as con:
        exists = con.execute(
            "SELECT username FROM users WHERE username=?",
            (receiver,)
        ).fetchone()

        if not exists:
            return jsonify({"error": "User not found"}), 404

        rows = con.execute("""
            SELECT id, sender, receiver, message, created
            FROM messages
            WHERE (sender=? AND receiver=?)
               OR (sender=? AND receiver=?)
            ORDER BY id DESC LIMIT 100
        """, (me, receiver, receiver, me)).fetchall()

    return jsonify([dict(row) for row in reversed(rows)])


CHAT_PAGE = """
<!DOCTYPE html>
<html>
<head>
<title>ConnectX Messenger</title>
<style>
* {box-sizing:border-box}
body {
    margin:0; background:#101522;
    color:white; font-family:Arial;
}
header {
    background:#1c2638; padding:18px;
    display:flex; justify-content:space-between;
}
header h2 {margin:0;color:#a99cff}
header a {color:#ff9292;text-decoration:none}
.container {
    max-width:850px; margin:25px auto;
    padding:15px;
}
.chat {
    background:#1c2638; border-radius:12px;
    padding:20px;
}
input,button {
    padding:12px; border:0;
    border-radius:7px;
}
input {
    background:#29364b;color:white;
}
button {
    background:#6755e8;color:white;
    cursor:pointer;
}
.top {
    display:flex; gap:10px;
}
.top input {flex:1;min-width:0}
#messages {
    height:390px; overflow-y:auto;
    background:#101522; padding:15px;
    margin:15px 0; border-radius:9px;
}
.msg {
    padding:11px; margin:8px 0;
    border-radius:10px; max-width:80%;
    overflow-wrap:anywhere;
}
.mine {background:#5748bd;margin-left:auto}
.theirs {background:#29364b}
.time {font-size:10px;color:#c5c5c5}
.send {display:flex;gap:10px}
.send input {flex:1;min-width:0}
</style>
</head>
<body>

<header>
<h2>ConnectX</h2>
<div>{{ username }} | <a href="/logout">Logout</a></div>
</header>

<div class="container">
<div class="chat">
<h2>Messages</h2>

<div class="top">
<input id="receiver" placeholder="Enter friend's username">
<button onclick="openChat()">Connect</button>
</div>

<p id="status">Enter a registered username to start chatting.</p>

<div id="messages"></div>

<div class="send">
<input id="message" placeholder="Write a message..."
onkeydown="if(event.key==='Enter') sendMessage()">
<button onclick="sendMessage()">Send</button>
</div>
</div>
</div>

<script>
const me = {{ username|tojson }};
let friend = "";

function openChat() {
    friend = document.getElementById("receiver").value.trim().toLowerCase();

    if (!friend || friend === me) {
        alert("Enter another user's username.");
        return;
    }

    document.getElementById("status").innerText =
        "Chatting with " + friend;
    loadMessages();
}

async function loadMessages() {
    if (!friend) return;

    try {
        let response = await fetch(
            "/api/messages?with=" + encodeURIComponent(friend)
        );

        let data = await response.json();

        if (!response.ok) {
            document.getElementById("status").innerText =
                data.error || "Could not load chat.";
            return;
        }

        let box = document.getElementById("messages");
        let oldHeight = box.scrollHeight;
        let nearBottom = box.scrollTop + box.clientHeight >= oldHeight - 50;

        box.replaceChildren();

        data.forEach(m => {
            let div = document.createElement("div");
            div.className = "msg " +
                (m.sender === me ? "mine" : "theirs");

            let sender = document.createElement("b");
            sender.textContent = m.sender;

            let content = document.createElement("div");
            content.textContent = m.message;

            let time = document.createElement("div");
            time.className = "time";
            time.textContent = m.created;

            div.append(sender, content, time);
            box.appendChild(div);
        });

        if (nearBottom) box.scrollTop = box.scrollHeight;

    } catch (error) {
        document.getElementById("status").innerText =
            "Internet connection problem.";
    }
}

async function sendMessage() {
    let input = document.getElementById("message");
    let message = input.value.trim();

    if (!friend || !message) return;

    try {
        let response = await fetch("/api/messages", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                receiver: friend,
                message: message
            })
        });

        let data = await response.json();

        if (!response.ok) {
            alert(data.error || "Message failed.");
            return;
        }

        input.value = "";
        await loadMessages();

    } catch (error) {
        alert("Check your internet connection.");
    }
}

setInterval(loadMessages, 2000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
