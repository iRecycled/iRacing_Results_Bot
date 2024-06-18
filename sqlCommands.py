import sqlite3

conn = sqlite3.connect('discord_bot.db')
cursor = conn.cursor()

def init():
    # Create a table for storing user_id, channel_id, last_race_time
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_channels (
                        id INTEGER PRIMARY KEY,
                        user_id TEXT,
                        channel_id TEXT,
                        last_race_time TEXT,
                        display_name TEXT
                    )''')
    conn.commit()

def save_user_channel(user_id, channel_id, display_name):
    try:
        cursor.execute("INSERT OR REPLACE INTO user_channels (user_id, channel_id, display_name) VALUES (?, ?, ?)", (str(user_id), str(channel_id), str(display_name)))
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        print(f"Failed to save user_id {user_id} and channel_id {channel_id}: {e}")
        return False
    
def remove_user_from_channel(user_id, channel_id):
    try:
        cursor.execute("DELETE FROM user_channels WHERE user_id=? AND channel_id=?", (str(user_id), str(channel_id)))
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        print(f"Failed to remove user_id {user_id}: {e}")
        return False

def save_user_last_race_time(user_id, last_race_time):
    try:
        cursor.execute("UPDATE user_channels SET last_race_time=? WHERE user_id=?", (last_race_time, user_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        print(f"Failed to update last_race_time for user_id {user_id}: {e}")
        return False
    
def save_user_display_name(user_id, display_name):
    try:
        cursor.execute("UPDATE user_channels SET display_name=? WHERE user_id=?", (display_name, user_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"Failed to update display_name for user_id {user_id}: {e}")

def get_display_name(user_id):
    cursor.execute("SELECT display_name FROM user_channels WHERE user_id=?", (str(user_id),))
    result = cursor.fetchone()
    return result[0] if result else None

def get_last_race_time(user_id):
    try:
        with sqlite3.connect('discord_bot.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_race_time FROM user_channels WHERE user_id=? LIMIT 1", (user_id,))
            result = cursor.fetchone()
            if result:
                return result[0]
            else:
                return None
    except sqlite3.Error as e:
        print(f"Failed to fetch last_race_time for user_id {user_id}: {e}")
        return None

def get_users_by_channel_id(channel_id):
    cursor.execute("SELECT user_id FROM user_channels WHERE channel_id=?", (str(channel_id),))
    result = cursor.fetchall()
    return [row[0] for row in result]

def get_all_channel_ids():
    cursor.execute("SELECT DISTINCT channel_id FROM user_channels")
    result = cursor.fetchall()
    if result is not None:
        return [row[0] for row in result]
    return None

def delete_all_records():
    try:
        with sqlite3.connect('discord_bot.db') as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_channels")
            conn.commit()
            print("All records deleted successfully.")
    except sqlite3.Error as e:
        print(f"Failed to delete records: {e}")