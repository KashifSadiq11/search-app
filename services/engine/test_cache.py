# migrate_data.py - Fixed version
import sqlite3
import psycopg2
from psycopg2.extras import execute_values

# Connect to both databases
sqlite_conn = sqlite3.connect('rec_engine.db')
pg_conn = psycopg2.connect("postgresql://postgres:123@localhost/rec_engine")

cursor_sqlite = sqlite_conn.cursor()
cursor_pg = pg_conn.cursor()

# Copy users
cursor_sqlite.execute("SELECT * FROM users")
users = cursor_sqlite.fetchall()
if users:
    execute_values(
        cursor_pg,
        "INSERT INTO users (id, username, email, created_at) VALUES %s ON CONFLICT DO NOTHING",
        users
    )

# Copy items - FIX THE BOOLEAN CONVERSION
cursor_sqlite.execute("SELECT * FROM items")
items = cursor_sqlite.fetchall()
if items:
    # Convert SQLite integers to PostgreSQL booleans
    items_fixed = []
    for item in items:
        # Assuming columns: id, title, description, category, brand, price, in_stock, ...
        items_fixed.append((
            item[0],  # id
            item[1],  # title
            item[2],  # description
            item[3],  # category
            item[4],  # brand
            item[5],  # price
            bool(item[6]) if item[6] is not None else True,  # in_stock - CONVERT TO BOOL
            item[7],  # popularity_score
            item[8],  # created_at
            # Add other columns as needed
        ))
    
    execute_values(
        cursor_pg,
        """INSERT INTO items (id, title, description, category, brand, price, in_stock, popularity_score, created_at) 
           VALUES %s ON CONFLICT DO NOTHING""",
        items_fixed
    )

# Copy interactions
cursor_sqlite.execute("SELECT * FROM interactions")
interactions = cursor_sqlite.fetchall()
if interactions:
    execute_values(
        cursor_pg,
        "INSERT INTO interactions (id, user_id, item_id, interaction_type, rating, created_at) VALUES %s ON CONFLICT DO NOTHING",
        interactions
    )

pg_conn.commit()
print("Data migrated successfully")

# Verify
cursor_pg.execute("SELECT COUNT(*) FROM users")
print(f"Users: {cursor_pg.fetchone()[0]}")
cursor_pg.execute("SELECT COUNT(*) FROM items")
print(f"Items: {cursor_pg.fetchone()[0]}")
cursor_pg.execute("SELECT COUNT(*) FROM interactions")
print(f"Interactions: {cursor_pg.fetchone()[0]}")

sqlite_conn.close()
pg_conn.close()