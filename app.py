import os
import random
import string
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
# ใช้ค่าจาก Environment Variable ตอน deploy จริง อย่า hardcode secret key ลงโค้ด
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dnd_web_secret_1234')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=1e7)

rooms = {}
# เก็บ mapping sid -> room_code เพื่อให้ตอน disconnect รู้ว่าต้องลบออกจากห้องไหน
sid_to_room = {}


def generate_room_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        if code not in rooms:
            return code


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('create_room')
def on_create_room():
    sid = request.sid
    room_code = generate_room_code()
    rooms[room_code] = {
        "game_started": False,
        "dm_sid": sid,
        "players": {},
        "active_combat_list": []
    }
    sid_to_room[sid] = room_code
    join_room(room_code)
    emit('room_created', {'room_code': room_code})


@socketio.on('join_room')
def on_join_room(data):
    sid = request.sid
    room_code = data.get('room_code', '').upper()

    if room_code not in rooms:
        emit('error_msg', {'message': 'ไม่พบรหัสห้องนี้!'})
        return

    sid_to_room[sid] = room_code
    join_room(room_code)
    is_dm = (sid == rooms[room_code]["dm_sid"])
    emit('room_joined', {'room_code': room_code, 'is_dm': is_dm})
    broadcast_game_state(room_code)


@socketio.on('submit_character')
def on_submit_character(data):
    sid = request.sid
    room_code = data.get('room_code')

    if room_code in rooms:
        rooms[room_code]["players"][sid] = {
            "sid": sid,
            "name": data.get('name'),
            "char_class": data.get('char_class', 'นักผจญภัย'),
            "stats": data.get('stats'),
            "avatar": data.get('avatar', ''),
            "skills": data.get('skills', []),
            "items": [],
            "hp": 20,
            "max_hp": 20,
            "level": 1,
            "gold": 100
        }
        broadcast_game_state(room_code)


@socketio.on('start_game')
def on_start_game(data):
    room_code = data.get('room_code')
    if room_code in rooms:
        rooms[room_code]["game_started"] = True
        broadcast_game_state(room_code)


@socketio.on('leave_room_event')
def on_leave_room(data):
    sid = request.sid
    room_code = data.get('room_code')
    _remove_player_from_room(sid, room_code)


@socketio.on('disconnect')
def on_disconnect():
    """
    สำคัญมาก: จับตอนที่ผู้เล่นปิดแท็บ/ปิดเบราว์เซอร์/เน็ตหลุด โดยไม่ได้กดปุ่ม
    'ย้อนกลับ' ก่อน ถ้าไม่มีตัวนี้ ผู้เล่นที่หลุดจะค้างอยู่ในห้องตลอดไป
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if room_code:
        _remove_player_from_room(sid, room_code)


def _remove_player_from_room(sid, room_code):
    if room_code not in rooms:
        sid_to_room.pop(sid, None)
        return

    leave_room(room_code)
    sid_to_room.pop(sid, None)

    if sid in rooms[room_code]["players"]:
        del rooms[room_code]["players"][sid]

    if sid == rooms[room_code]["dm_sid"]:
        del rooms[room_code]
        emit('room_closed', room=room_code)
    else:
        broadcast_game_state(room_code)


@socketio.on('send_global_chat')
def on_send_global_chat(data):
    sid = request.sid
    room_code = data.get('room_code')
    msg = data.get('message', '').strip()

    if room_code in rooms and msg:
        is_dm = (sid == rooms[room_code]["dm_sid"])
        sender_name = "👑 [Dungeon Master]" if is_dm else rooms[room_code]["players"].get(sid, {}).get('name', 'ผู้เล่น')

        emit('receive_global_chat', {
            'sender': sender_name,
            'message': msg,
            'is_dm': is_dm
        }, room=room_code)


@socketio.on('update_player_hp')
def on_update_player_hp(data):
    room_code = data.get('room_code')
    target_sid = data.get('target_sid')
    hp_change = int(data.get('hp_change', 0))

    if room_code in rooms and target_sid in rooms[room_code]["players"]:
        player = rooms[room_code]["players"][target_sid]
        player["hp"] = max(0, min(player["max_hp"], player["hp"] + hp_change))
        broadcast_game_state(room_code)


@socketio.on('update_player_gold')
def on_update_player_gold(data):
    room_code = data.get('room_code')
    target_sid = data.get('target_sid')
    gold_change = int(data.get('gold_change', 0))

    if room_code in rooms:
        if target_sid == "ALL":
            for p in rooms[room_code]["players"].values():
                p["gold"] = max(0, p["gold"] + gold_change)
        elif target_sid in rooms[room_code]["players"]:
            player = rooms[room_code]["players"][target_sid]
            player["gold"] = max(0, player["gold"] + gold_change)

        broadcast_game_state(room_code)


@socketio.on('give_item')
def on_give_item(data):
    room_code = data.get('room_code')
    target_sid = data.get('target_sid')
    item_name = data.get('name', '').strip()
    item_desc = data.get('description', '').strip()

    if room_code in rooms and item_name:
        item = {"name": item_name, "desc": item_desc}

        if target_sid == "ALL":
            for p in rooms[room_code]["players"].values():
                p["items"].append(item)
            msg = f"🎁 DM ได้มอบไอเทม **[{item_name}]** ให้กับผู้เล่นทุกคนในปาร์ตี้!"
        elif target_sid in rooms[room_code]["players"]:
            player = rooms[room_code]["players"][target_sid]
            player["items"].append(item)
            msg = f"🎁 DM ได้มอบไอเทม **[{item_name}]** ให้กับ **{player['name']}**!"
        else:
            return

        broadcast_game_state(room_code)
        emit('receive_global_chat', {
            'sender': '👑 [Dungeon Master]',
            'message': msg,
            'is_dm': True
        }, room=room_code)


@socketio.on('use_item')
def on_use_item(data):
    sid = request.sid
    room_code = data.get('room_code')
    item_index = data.get('item_index')

    if room_code in rooms and sid in rooms[room_code]["players"]:
        player = rooms[room_code]["players"][sid]
        items = player.get("items", [])

        if 0 <= item_index < len(items):
            used_item = items.pop(item_index)
            broadcast_game_state(room_code)

            emit('receive_global_chat', {
                'sender': '✨ ระบบไอเทม',
                'message': f"🧪 **{player['name']}** ได้กดใช้ไอเทม **[{used_item['name']}]** ({used_item.get('desc', '')})",
                'is_dm': False
            }, room=room_code)


@socketio.on('update_player_level')
def on_update_player_level(data):
    room_code = data.get('room_code')
    target_sid = data.get('target_sid')
    lvl_change = int(data.get('lvl_change', 0))

    if room_code in rooms and target_sid in rooms[room_code]["players"]:
        player = rooms[room_code]["players"][target_sid]
        player["level"] = max(1, player["level"] + lvl_change)
        player["max_hp"] += (lvl_change * 5)
        player["hp"] = player["max_hp"]
        broadcast_game_state(room_code)


@socketio.on('transfer_gold')
def on_transfer_gold(data):
    sender_sid = request.sid
    room_code = data.get('room_code')
    receiver_sid = data.get('receiver_sid')
    amount = int(data.get('amount', 0))

    if room_code in rooms and sender_sid in rooms[room_code]["players"] and receiver_sid in rooms[room_code]["players"]:
        sender = rooms[room_code]["players"][sender_sid]
        receiver = rooms[room_code]["players"][receiver_sid]

        if amount > 0 and sender["gold"] >= amount:
            sender["gold"] -= amount
            receiver["gold"] += amount
            broadcast_game_state(room_code)
            emit('receive_global_chat', {
                'sender': '💰 ระบบธนาคาร',
                'message': f"💸 **{sender['name']}** โอนเงิน **{amount} G** ให้กับ **{receiver['name']}** เรียบร้อย!",
                'is_dm': False
            }, room=room_code)
        else:
            emit('error_msg', {'message': 'เงินไม่พอโอน หรือจำนวนเงินไม่ถูกต้อง!'})


@socketio.on('add_monster_event')
def on_add_monster_event(data):
    room_code = data.get('room_code')
    if room_code in rooms:
        monster = {
            "id": ''.join(random.choices(string.ascii_lowercase + string.digits, k=6)),
            "name": data.get('name'),
            "hp": int(data.get('hp', 50)),
            "max_hp": int(data.get('hp', 50)),
            "attack": int(data.get('attack', 10)),
            "avatar": data.get('avatar', '')
        }
        rooms[room_code]["active_combat_list"].append(monster)
        broadcast_game_state(room_code)
        emit('receive_global_chat', {
            'sender': '👑 [Dungeon Master]',
            'message': f"⚠️ ศัตรูปรากฏตัว: **{monster['name']}** (HP: {monster['hp']})!",
            'is_dm': True
        }, room=room_code)


@socketio.on('clear_combat_events')
def on_clear_combat_events(data):
    room_code = data.get('room_code')
    if room_code in rooms:
        rooms[room_code]["active_combat_list"] = []
        broadcast_game_state(room_code)


def broadcast_game_state(room_code):
    if room_code in rooms:
        all_players = list(rooms[room_code]["players"].values())
        combat_list = rooms[room_code]["active_combat_list"]
        game_started = rooms[room_code]["game_started"]

        emit('game_state_update', {
            'players': all_players,
            'combat_list': combat_list,
            'game_started': game_started
        }, room=room_code)
        emit('update_player_list', {'players': all_players}, room=room_code)


@socketio.on('roll_dice')
def on_roll_dice(data):
    sid = request.sid
    room_code = data.get('room_code')
    dice_type = int(data.get('dice_type', 20))
    stat_used = data.get('stat_used', 'NONE')

    if room_code in rooms:
        is_dm = (sid == rooms[room_code]["dm_sid"])
        player_name = "Dungeon Master" if is_dm else rooms[room_code]["players"].get(sid, {}).get('name', 'ผู้เล่น')
        stat_bonus = 0
        if not is_dm and sid in rooms[room_code]["players"]:
            player = rooms[room_code]["players"][sid]
            stat_bonus = player["stats"].get(stat_used, 0) if stat_used != 'NONE' else 0

        raw_roll = random.randint(1, dice_type)
        total = raw_roll + stat_bonus

        emit('dice_rolled_event', {
            "player_name": player_name,
            "dice_type": dice_type,
            "raw_roll": raw_roll,
            "stat_used": stat_used,
            "stat_bonus": stat_bonus,
            "total": total
        }, room=room_code)


if __name__ == '__main__':
    # host='0.0.0.0' จำเป็นเวลา deploy จริง เพื่อให้เครื่องอื่นต่อเข้ามาได้
    # PORT อ่านจาก environment variable เพราะ hosting ส่วนใหญ่ (Render/Railway ฯลฯ) กำหนด PORT ให้เอง
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host='0.0.0.0', port=port, debug=debug_mode)
