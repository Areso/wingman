#!/bin/bash

# Define the database name
DB_NAME="wingman.db"

# Check if the database file already exists
if [ ! -f "$DB_NAME" ]; then
    echo "Database '$DB_NAME' not found. Creating it now..."
    # Simply touching the file initializes it, though sqlite3 would do this anyway
    touch "$DB_NAME"
else
    echo "Database '$DB_NAME' already exists. Appending settings..."
fi

# SQL command to create the table and insert the records
sqlite3 "$DB_NAME" <<EOF
CREATE TABLE IF NOT EXISTS wingman_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    s_key TEXT NOT NULL UNIQUE,
    s_value TEXT NOT NULL
);

INSERT INTO wingman_settings (s_key, s_value) VALUES ('default_channel', 'telegram');
INSERT INTO wingman_settings (s_key, s_value) VALUES ('send_empty_results', 'false');
EOF

echo "Setup complete! Default settings inserted into '$DB_NAME'."
