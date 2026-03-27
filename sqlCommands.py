import sqlite3
import threading

# Thread-local storage for database connections
_thread_local = threading.local()


def get_connection():
    """Get a thread-local database connection."""
    if not hasattr(_thread_local, "conn"):
        _thread_local.conn = sqlite3.connect("discord_bot.db", check_same_thread=False)
    return _thread_local.conn


def get_cursor():
    """Get a cursor from the thread-local connection."""
    return get_connection().cursor()


def init():
    # Create a table for storing user_id, channel_id, last_race_time
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS user_channels (
                        id INTEGER PRIMARY KEY,
                        user_id TEXT,
                        channel_id TEXT,
                        last_race_time TEXT,
                        display_name TEXT
                    )"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS league_subscriptions (
                        id INTEGER PRIMARY KEY,
                        league_id INTEGER,
                        season_id INTEGER,
                        channel_id TEXT,
                        cust_id TEXT,
                        last_subsession_id INTEGER,
                        UNIQUE(league_id, channel_id, cust_id)
                    )"""
    )
    conn.commit()


def save_user_channel(user_id, channel_id, display_name):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM user_channels WHERE user_id = ? AND channel_id = ?",
            (str(user_id), str(channel_id)),
        )
        exists = cursor.fetchone()

        if exists:
            return True

        cursor.execute(
            "INSERT INTO user_channels (user_id, channel_id, display_name) VALUES (?, ?, ?)",
            (str(user_id), str(channel_id), str(display_name)),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        print(f"Failed to save user_id {user_id} and channel_id {channel_id}: {e}")
        return False


def remove_user_from_channel(user_id, channel_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM user_channels WHERE user_id=? AND channel_id=?",
            (str(user_id), str(channel_id)),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        print(f"Failed to remove user_id {user_id}: {e}")
        return False


def save_user_last_race_time(user_id, last_race_time, channel_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE user_channels SET last_race_time=? WHERE user_id=? AND channel_id=?",
            (last_race_time, user_id, channel_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        print(f"Failed to update last_race_time for user_id {user_id}: {e}")
        return False


def save_user_display_name(user_id, display_name):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE user_channels SET display_name=? WHERE user_id=?",
            (display_name, user_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"Failed to update display_name for user_id {user_id}: {e}")


def get_display_name(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT display_name FROM user_channels WHERE user_id=?", (str(user_id),))
    result = cursor.fetchone()
    return result[0] if result else None


def get_last_race_time(user_id, channel_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_race_time FROM user_channels WHERE user_id=? AND channel_id=? LIMIT 1",
            (user_id, channel_id),
        )
        result = cursor.fetchone()
        if result:
            return result[0]
        else:
            return None
    except sqlite3.Error as e:
        print(f"Failed to fetch last_race_time for user_id {user_id}: {e}")
        return None


def get_users_by_channel_id(channel_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM user_channels WHERE channel_id=?", (str(channel_id),))
    result = cursor.fetchall()
    return [row[0] for row in result]


def get_all_channel_ids():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT channel_id FROM user_channels")
    result = cursor.fetchall()
    if result is not None:
        return [row[0] for row in result]
    return None


def get_all_user_channel_pairs():
    """Get all unique (user_id, channel_id) pairs for batch processing."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, channel_id FROM user_channels")
    result = cursor.fetchall()
    return [(row[0], row[1]) for row in result] if result else []


def save_league_subscription(league_id, season_id, channel_id, cust_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR IGNORE INTO league_subscriptions (league_id, season_id, channel_id, cust_id)
               VALUES (?, ?, ?, ?)""",
            (league_id, season_id, str(channel_id), str(cust_id)),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Failed to save league subscription: {e}")
        return False


def remove_league_subscription(league_id, channel_id, cust_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM league_subscriptions WHERE league_id=? AND channel_id=? AND cust_id=?",
            (league_id, str(channel_id), str(cust_id)),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Failed to remove league subscription: {e}")
        return False


def get_unique_leagues():
    """Return distinct (league_id, season_id) pairs being tracked."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT league_id, season_id FROM league_subscriptions")
    return cursor.fetchall() or []


def get_subscriptions_for_league(league_id):
    """Return (channel_id, cust_id, last_subsession_id) for all subscriptions to a league."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT channel_id, cust_id, last_subsession_id FROM league_subscriptions WHERE league_id=?",
        (league_id,),
    )
    return cursor.fetchall() or []


def update_league_season_id(league_id, new_season_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE league_subscriptions SET season_id=? WHERE league_id=?",
            (new_season_id, league_id),
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Failed to update season_id for league {league_id}: {e}")


def update_league_last_subsession(league_id, channel_id, cust_id, subsession_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE league_subscriptions SET last_subsession_id=?
               WHERE league_id=? AND channel_id=? AND cust_id=?""",
            (subsession_id, league_id, str(channel_id), str(cust_id)),
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Failed to update last_subsession_id: {e}")


def delete_all_records():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_channels")
        conn.commit()
        print("All records deleted successfully.")
    except sqlite3.Error as e:
        print(f"Failed to delete records: {e}")
