#!/bin/bash

DB_NAME="discord_bot.db"
TABLE_NAME="user_channels"
BACKUP_FILE="/tmp/${TABLE_NAME}_backup.csv"

# Export the existing data
sqlite3 $DB_NAME <<EOF
.mode csv
.output $BACKUP_FILE
SELECT * FROM $TABLE_NAME;
EOF

# Drop the existing table
sqlite3 $DB_NAME <<EOF
DROP TABLE $TABLE_NAME;
EOF

# Create the new table without the UNIQUE constraint
sqlite3 $DB_NAME <<EOF
CREATE TABLE $TABLE_NAME (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    channel_id TEXT,
    last_race_time TEXT,
    display_name TEXT
);
EOF

# Import the data back into the new table
sqlite3 $DB_NAME <<EOF
.mode csv
.import $BACKUP_FILE $TABLE_NAME
EOF

echo "Field 'user_id' is now not unique."
