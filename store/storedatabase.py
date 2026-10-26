import os
import sqlite3
from contextlib import closing


DATABASE_PATH = os.environ.get("DATABASE_PATH", "store.db")


def _connect():
    return sqlite3.connect(DATABASE_PATH, timeout=10)


def add_basket(itemid, product, colour, size, quantity, sessionid, price):
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT INTO basketitems "
            "(itemid, product, colour, size, quantity, sessionid, price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (itemid, product, colour, size, quantity, sessionid, price),
        )


def retrieve(sessionid):
    with closing(_connect()) as connection, connection:
        return connection.execute(
            "SELECT product, colour, size, quantity, price "
            "FROM basketitems WHERE sessionid = ?",
            (sessionid,),
        ).fetchall()


def retrieveprice(sessionid):
    with closing(_connect()) as connection, connection:
        return connection.execute(
            "SELECT quantity, itemid FROM basketitems WHERE sessionid = ?",
            (sessionid,),
        ).fetchall()


def searchproducts(query):
    with closing(_connect()) as connection, connection:
        data = connection.execute(
            "SELECT product FROM products WHERE INSTR(product, ?) > 0",
            (query,),
        ).fetchall()
    return [data]


def claim_order_notification(stripe_session_id):
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS order_notifications (
                stripe_session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor = connection.execute(
            """
            INSERT INTO order_notifications (stripe_session_id, status)
            VALUES (?, 'sending')
            ON CONFLICT(stripe_session_id) DO UPDATE SET
                status = 'sending',
                updated_at = CURRENT_TIMESTAMP
            WHERE order_notifications.status != 'sent'
              AND order_notifications.updated_at <= datetime('now', '-5 minutes')
            """,
            (stripe_session_id,),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def complete_order_notification(stripe_session_id):
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            UPDATE order_notifications
            SET status = 'sent', updated_at = CURRENT_TIMESTAMP
            WHERE stripe_session_id = ? AND status = 'sending'
            """,
            (stripe_session_id,),
        )


def release_order_notification(stripe_session_id):
    with closing(_connect()) as connection, connection:
        connection.execute(
            "DELETE FROM order_notifications "
            "WHERE stripe_session_id = ? AND status = 'sending'",
            (stripe_session_id,),
        )
