import os
import io
import psycopg
from psycopg.rows import dict_row
from psycopg import errors
from PIL import Image
from flask import (
    Flask, request, redirect, session, jsonify,
    render_template_string, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Add your Neon connection string in Render Environment settings.")


def connect_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def setup():
    with connect_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                photo BYTEA,
                photo_type TEXT,
                last_seen TIMESTAMPTZ
            )
        """)
        con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS photo BYTEA")
        con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_type TEXT")
        con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ")

        con.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                sender TEXT NOT NULL,
                receiver TEXT NOT NULL,
                message TEXT NOT NULL,
                created TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS friend_requests (
                id BIGSERIAL PRIMARY KEY,
                sender TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                receiver TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                created TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sender, receiver)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS chat_groups (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                owner TEXT NOT NULL REFERENCES users(username),
                created TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id BIGINT NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                PRIMARY KEY (group_id, username)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS group_messages (
                id BIGSERIAL PRIMARY KEY,
                group_id BIGINT NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                sender TEXT NOT NULL REFERENCES users(username),
                message TEXT NOT NULL,
                created TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)


setup()


def current_user():
    username = session.get("user")
    if username:
        with connect_db() as con:
            con.execute(
                "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE username = %s",
                (username,)
            )
    return username


def error(message, status=400):
    return jsonify({"error": message}), status


LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ConnectX</title>
<style>
*{box-sizing:border-box}
body{
 background:#101522;color:white;font-family:Arial;
 display:flex;justify-content:center;align-items:center;
 min-height:100vh;margin:0
}
.box{
 background:#1c2638;padding:32px;border-radius:16px;
 width:min(340px,92vw);text-align:center
}
input,button{
 width:100%;padding:13px;margin:9px 0;
 border:0;border-radius:8px
}
input{background:#29364b;color:white}
button{background:#6755e8;color:white;cursor:pointer}
a{color:#b5aaff}
.error{color:#ff8888}
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
<p class="error">{{ error }}</p>
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
    username = current_user()
    if not username:
        return redirect("/login")
    return render_template_string(CHAT_PAGE, username=username)


@app.route("/register", methods=["GET", "POST"])
def register():
    message = ""

    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        if not username.isalnum() or len(username) < 3:
            message = "Username must be at least 3 letters or numbers."
        elif len(password) < 8:
            message = "Password must have at least 8 characters."
        else:
            try:
                with connect_db() as con:
                    con.execute(
                        """INSERT INTO users
                        (username, password, last_seen)
                        VALUES (%s, %s, CURRENT_TIMESTAMP)""",
                        (username, generate_password_hash(password))
                    )
                session["user"] = username
                return redirect("/")
            except errors.UniqueViolation:
                message = "Username already exists."

    return render_template_string(
        LOGIN_PAGE, heading="Register",
        error=message, register=True
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    message = ""

    if request.method == "POST":
        username = request.form["username"].strip().lower()

        with connect_db() as con:
            user = con.execute(
                "SELECT * FROM users WHERE username = %s",
                (username,)
            ).fetchone()

        if user and check_password_hash(
            user["password"], request.form["password"]
        ):
            session["user"] = username
            current_user()
            return redirect("/")

        message = "Incorrect username or password."

    return render_template_string(
        LOGIN_PAGE, heading="Login",
        error=message, register=False
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/avatar/<username>")
def avatar(username):
    with connect_db() as con:
        user = con.execute(
            "SELECT photo, photo_type FROM users WHERE username = %s",
            (username.lower(),)
        ).fetchone()

    if not user or not user["photo"]:
        return "", 404

    return Response(
        user["photo"],
        mimetype=user["photo_type"] or "image/jpeg",
        headers={"Cache-Control": "no-store"}
    )


@app.route("/api/profile/photo", methods=["POST"])
def upload_photo():
    me = current_user()
    if not me:
        return error("Login required", 401)

    file = request.files.get("photo")
    if not file or not file.filename:
        return error("Choose an image first.")

    try:
        raw = file.read(2 * 1024 * 1024 + 1)

        if len(raw) > 2 * 1024 * 1024:
            return error("Image must be 2 MB or smaller.")

        image = Image.open(io.BytesIO(raw))
        image.thumbnail((512, 512))

        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        photo = output.getvalue()

    except Exception:
        return error("Please upload a valid image (JPG, PNG or WEBP).")

    with connect_db() as con:
        con.execute(
            """UPDATE users SET photo = %s,
            photo_type = 'image/jpeg' WHERE username = %s""",
            (photo, me)
        )

    return jsonify({"status": "uploaded"})@app.route("/api/users/search")
def search_users():
    me = current_user()
    if not me:
        return error("Login required", 401)

    query = request.args.get("q", "").strip().lower()

    if len(query) < 2:
        return jsonify([])

    with connect_db() as con:
        users = con.execute("""
            SELECT username,
            (last_seen > CURRENT_TIMESTAMP - INTERVAL '2 minutes') AS online
            FROM users
            WHERE username LIKE %s AND username != %s
            ORDER BY username LIMIT 20
        """, ("%" + query + "%", me)).fetchall()

    return jsonify(users)


@app.route("/api/friends")
def get_friends():
    me = current_user()
    if not me:
        return error("Login required", 401)

    with connect_db() as con:
        friends = con.execute("""
            SELECT u.username,
            (u.last_seen > CURRENT_TIMESTAMP - INTERVAL '2 minutes') AS online
            FROM friend_requests f
            JOIN users u
            ON u.username = CASE
                WHEN f.sender = %s THEN f.receiver
                ELSE f.sender END
            WHERE (f.sender = %s OR f.receiver = %s)
            AND f.status = 'accepted'
            ORDER BY u.username
        """, (me, me, me)).fetchall()

        requests = con.execute("""
            SELECT id, sender FROM friend_requests
            WHERE receiver = %s AND status = 'pending'
            ORDER BY created DESC
        """, (me,)).fetchall()

    return jsonify({
        "friends": friends,
        "requests": requests
    })


@app.route("/api/friends/request", methods=["POST"])
def send_friend_request():
    me = current_user()
    if not me:
        return error("Login required", 401)

    username = request.json.get("username", "").strip().lower()

    if username == me:
        return error("You cannot add yourself.")

    with connect_db() as con:
        exists = con.execute(
            "SELECT username FROM users WHERE username = %s",
            (username,)
        ).fetchone()

        if not exists:
            return error("User not found.")

        already = con.execute("""
            SELECT id FROM friend_requests
            WHERE (sender = %s AND receiver = %s)
            OR (sender = %s AND receiver = %s)
        """, (me, username, username, me)).fetchone()

        if already:
            return error("A request or friendship already exists.")

        con.execute("""
            INSERT INTO friend_requests(sender, receiver)
            VALUES (%s, %s)
        """, (me, username))

    return jsonify({"status": "request sent"})


@app.route("/api/friends/respond", methods=["POST"])
def respond_friend_request():
    me = current_user()
    if not me:
        return error("Login required", 401)

    data = request.json
    request_id = data.get("id")
    action = data.get("action")

    if action not in ("accept", "reject"):
        return error("Invalid action.")

    with connect_db() as con:
        result = con.execute("""
            UPDATE friend_requests
            SET status = %s
            WHERE id = %s AND receiver = %s
            AND status = 'pending'
            RETURNING id
        """, (
            "accepted" if action == "accept" else "rejected",
            request_id, me
        )).fetchone()

    if not result:
        return error("Request not found.")

    return jsonify({"status": action})


@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    me = current_user()
    if not me:
        return error("Login required", 401)

    if request.method == "POST":
        data = request.json or {}
        receiver = data.get("receiver", "").strip().lower()
        message = data.get("message", "").strip()

        if not receiver or not message:
            return error("Receiver and message are required.")

        if len(message) > 2000:
            return error("Message is too long.")

        with connect_db() as con:
            exists = con.execute(
                "SELECT username FROM users WHERE username = %s",
                (receiver,)
            ).fetchone()

            if not exists:
                return error("User not found.")

            con.execute("""
                INSERT INTO messages(sender, receiver, message)
                VALUES (%s, %s, %s)
            """, (me, receiver, message))

        return jsonify({"status": "sent"})

    other = request.args.get("with", "").strip().lower()

    if not other:
        return error("Choose a user.")

    with connect_db() as con:
        chat = con.execute("""
            SELECT id, sender, receiver, message, created
            FROM messages
            WHERE (sender = %s AND receiver = %s)
            OR (sender = %s AND receiver = %s)
            ORDER BY created ASC LIMIT 200
        """, (me, other, other, me)).fetchall()

    return jsonify(chat)


@app.route("/api/groups", methods=["GET", "POST"])
def groups():
    me = current_user()
    if not me:
        return error("Login required", 401)

    if request.method == "POST":
        data = request.json or {}
        name = data.get("name", "").strip()
        members = data.get("members", [])

        if not name:
            return error("Enter a group name.")

        if len(name) > 50:
            return error("Group name is too long.")

        with connect_db() as con:
            group = con.execute("""
                INSERT INTO chat_groups(name, owner)
                VALUES (%s, %s) RETURNING id
            """, (name, me)).fetchone()

            group_id = group["id"]

            con.execute("""
                INSERT INTO group_members(group_id, username)
                VALUES (%s, %s)
            """, (group_id, me))

            for member in set(members):
                member = str(member).strip().lower()

                if member == me:
                    continue

                friend = con.execute("""
                    SELECT id FROM friend_requests
                    WHERE ((sender = %s AND receiver = %s)
                    OR (sender = %s AND receiver = %s))
                    AND status = 'accepted'
                """, (me, member, member, me)).fetchone()

                if friend:
                    con.execute("""
                        INSERT INTO group_members(group_id, username)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (group_id, member))

        return jsonify({"status": "created", "id": group_id})

    with connect_db() as con:
        result = con.execute("""
            SELECT g.id, g.name, g.owner
            FROM chat_groups g
            JOIN group_members m ON m.group_id = g.id
            WHERE m.username = %s
            ORDER BY g.created DESC
        """, (me,)).fetchall()

    return jsonify(result)


@app.route("/api/groups/<int:group_id>/messages",
           methods=["GET", "POST"])
def group_chat(group_id):
    me = current_user()
    if not me:
        return error("Login required", 401)

    with connect_db() as con:
        member = con.execute("""
            SELECT username FROM group_members
            WHERE group_id = %s AND username = %s
        """, (group_id, me)).fetchone()

        if not member:
            return error("You are not a member of this group.", 403)

        if request.method == "POST":
            data = request.json or {}
            message = data.get("message", "").strip()

            if not message:
                return error("Message cannot be empty.")

            if len(message) > 2000:
                return error("Message is too long.")

            con.execute("""
                INSERT INTO group_messages(group_id, sender, message)
                VALUES (%s, %s, %s)
            """, (group_id, me, message))

            return jsonify({"status": "sent"})

        result = con.execute("""
            SELECT id, sender, message, created
            FROM group_messages
            WHERE group_id = %s
            ORDER BY created ASC LIMIT 200
        """, (group_id,)).fetchall()

    return jsonify(result)CHAT_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ConnectX</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#101522;color:white;font-family:Arial}
header{background:#1c2638;padding:15px;display:flex;
justify-content:space-between;align-items:center}
header h2{margin:0;color:#a99cff}
button{background:#6755e8;color:white;border:0;
padding:10px;border-radius:7px;cursor:pointer}
input{background:#29364b;color:white;border:1px solid #45536b;
padding:11px;border-radius:7px}
.layout{display:grid;grid-template-columns:300px 1fr;
height:calc(100vh - 55px)}
aside{background:#1c2638;padding:15px;overflow:auto}
main{padding:18px;display:flex;flex-direction:column;min-width:0}
.section{border-bottom:1px solid #39465c;padding:12px 0}
.item{padding:9px;margin:5px 0;background:#29364b;
border-radius:7px;cursor:pointer}
.item:hover{background:#394b68}
#messages{flex:1;overflow:auto;background:#182235;
padding:12px;border-radius:10px;margin:12px 0}
.msg{padding:9px;margin:7px 0;border-radius:8px;
background:#29364b;max-width:80%;overflow-wrap:anywhere}
.mine{background:#5142a9;margin-left:auto}
.send{display:flex;gap:8px}
.send input{flex:1;min-width:0}
#status{color:#a99cff}
img.avatar{width:42px;height:42px;border-radius:50%;
object-fit:cover;vertical-align:middle;margin-right:8px}
.small{font-size:12px;color:#bbb}
@media(max-width:650px){
.layout{grid-template-columns:1fr;height:auto}
aside{max-height:45vh}
main{height:60vh}
}
</style>
</head>
<body>
<header>
<h2>ConnectX</h2>
<div>{{ username }} |
<a href="/logout" style="color:white">Logout</a></div>
</header>

<div class="layout">
<aside>
<div class="section">
<h3>My Profile</h3>
<img class="avatar" src="/avatar/{{ username }}"
onerror="this.style.display='none'">
<strong>{{ username }}</strong>
<br><br>
<input type="file" id="photo" accept="image/*">
<button onclick="uploadPhoto()">Upload Photo</button>
</div>

<div class="section">
<h3>Find People</h3>
<input id="search" placeholder="Search username..."
oninput="searchUsers()">
<div id="searchResults"></div>
</div>

<div class="section">
<h3>Friend Requests</h3>
<div id="requests"></div>
</div>

<div class="section">
<h3>Friends</h3>
<div id="friends"></div>
</div>

<div class="section">
<h3>Groups</h3>
<input id="groupName" placeholder="Group name">
<input id="groupMembers" placeholder="Friend usernames, comma separated">
<button onclick="createGroup()">Create Group</button>
<div id="groups"></div>
</div>
</aside>

<main>
<h3 id="chatTitle">Welcome to ConnectX</h3>
<div id="status">Select a friend or group to start chatting.</div>
<div id="messages"></div>
<div class="send">
<input id="message" placeholder="Write a message..."
onkeydown="if(event.key==='Enter')sendMessage()">
<button onclick="sendMessage()">Send</button>
</div>
</main>
</div>

<script>
let currentChat = null;
let chatType = null;

async function api(url, options={}) {
    const response = await fetch(url, {
        headers: {"Content-Type":"application/json"},
        ...options
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Something went wrong");
    return data;
}

async function uploadPhoto() {
    const file = document.getElementById("photo").files[0];
    if (!file) return alert("Choose an image first.");

    const form = new FormData();
    form.append("photo", file);

    try {
        const response = await fetch("/api/profile/photo", {
            method:"POST", body:form
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        alert("Profile photo uploaded!");
        location.reload();
    } catch(e) {
        alert(e.message);
    }
}

async function searchUsers() {
    const q = document.getElementById("search").value.trim();
    const box = document.getElementById("searchResults");
    box.innerHTML = "";
    if (q.length < 2) return;

    try {
        const users = await api("/api/users/search?q="+encodeURIComponent(q));
        users.forEach(user => {
            const div = document.createElement("div");
            div.className = "item";
            div.textContent = user.username + (user.online ? " • Online" : " • Offline");
            const button = document.createElement("button");
            button.textContent = "Add";
            button.onclick = () => sendRequest(user.username);
            div.appendChild(button);
            box.appendChild(div);
        });
    } catch(e) { console.log(e); }
}

async function sendRequest(username) {
    try {
        await api("/api/friends/request", {
            method:"POST",
            body:JSON.stringify({username})
        });
        alert("Friend request sent.");
    } catch(e) { alert(e.message); }
}

async function loadFriends() {
    try {
        const data = await api("/api/friends");

        const friendBox = document.getElementById("friends");
        friendBox.innerHTML = "";

        data.friends.forEach(friend => {
            const div = document.createElement("div");
            div.className = "item";
            div.textContent = friend.username +
                (friend.online ? " • Online" : " • Offline");
            div.onclick = () => openChat(friend.username);
            friendBox.appendChild(div);
        });

        const requestBox = document.getElementById("requests");
        requestBox.innerHTML = "";

        data.requests.forEach(req => {
            const div = document.createElement("div");
            div.className = "item";
            div.textContent = req.sender + " ";

            const accept = document.createElement("button");
            accept.textContent = "Accept";
            accept.onclick = () => respond(req.id, "accept");

            const reject = document.createElement("button");
            reject.textContent = "Reject";
            reject.onclick = () => respond(req.id, "reject");

            div.append(accept, reject);
            requestBox.appendChild(div);
        });
    } catch(e) { console.log(e); }
}

async function respond(id, action) {
    try {
        await api("/api/friends/respond", {
            method:"POST",
            body:JSON.stringify({id, action})
        });
        loadFriends();
    } catch(e) { alert(e.message); }
}

async function loadGroups() {
    try {
        const groups = await api("/api/groups");
        const box = document.getElementById("groups");
        box.innerHTML = "";

        groups.forEach(group => {
            const div = document.createElement("div");
            div.className = "item";
            div.textContent = "👥 " + group.name;
            div.onclick = () => openGroup(group.id, group.name);
            box.appendChild(div);
        });
    } catch(e) { console.log(e); }
}

async function createGroup() {
    const name = document.getElementById("groupName").value.trim();
    const members = document.getElementById("groupMembers").value
        .split(",").map(x => x.trim()).filter(Boolean);

    if (!name) return alert("Enter a group name.");

    try {
        await api("/api/groups", {
            method:"POST",
            body:JSON.stringify({name, members})
        });
        document.getElementById("groupName").value = "";
        document.getElementById("groupMembers").value = "";
        loadGroups();
        alert("Group created!");
    } catch(e) { alert(e.message); }
}

function openChat(username) {
    currentChat = username;
    chatType = "direct";
    document.getElementById("chatTitle").textContent = "Chat with " + username;
    document.getElementById("status").textContent = "Direct message";
    loadMessages();
}

function openGroup(id, name) {
    currentChat = id;
    chatType = "group";
    document.getElementById("chatTitle").textContent = "Group: " + name;
    document.getElementById("status").textContent = "Group chat";
    loadMessages();
}

async function loadMessages() {
    if (!currentChat) return;

    try {
        let url = chatType === "direct"
            ? "/api/messages?with="+encodeURIComponent(currentChat)
            : "/api/groups/"+currentChat+"/messages";

        const messages = await api(url);
        const box = document.getElementById("messages");
        box.innerHTML = "";

        messages.forEach(msg => {
            const div = document.createElement("div");
            div.className = "msg" +
                (msg.sender === "{{ username }}" ? " mine" : "");

            const sender = document.createElement("strong");
            sender.textContent = msg.sender + ": ";

            const content = document.createElement("span");
            content.textContent = msg.message;

            div.append(sender, content);
            box.appendChild(div);
        });

        box.scrollTop = box.scrollHeight;
    } catch(e) { console.log(e); }
}

async function sendMessage() {
    const input = document.getElementById("message");
    const message = input.value.trim();

    if (!message || !currentChat) {
        if (!currentChat) alert("Select a friend or group first.");
        return;
    }

    try {
        const url = chatType === "direct"
            ? "/api/messages"
            : "/api/groups/"+currentChat+"/messages";

        const body = chatType === "direct"
            ? {receiver:currentChat, message}
            : {message};

        await api(url, {
            method:"POST",
            body:JSON.stringify(body)
        });

        input.value = "";
        loadMessages();
    } catch(e) { alert(e.message); }
}

loadFriends();
loadGroups();
setInterval(loadFriends, 15000);
setInterval(loadGroups, 20000);
setInterval(loadMessages, 3000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
