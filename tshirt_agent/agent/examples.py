"""Worked examples of question → SQL for this store, shown to the agent as patterns.

They replace the old few_shots.py: same question types, but the SQL is correct for both
SQLite and MySQL, and there are no made-up answers (the agent must run the query).
"""

SQL_EXAMPLES = [
    (
        "How many t-shirts do we have left for Nike in XS size and white color?",
        "SELECT SUM(stock_quantity) FROM t_shirts WHERE brand = 'Nike' AND color = 'White' AND size = 'XS'",
    ),
    (
        "How much is the total price of the inventory for all S-size t-shirts?",
        "SELECT SUM(price * stock_quantity) FROM t_shirts WHERE size = 'S'",
    ),
    (
        "If we sell all Levi's t-shirts today with discounts applied, how much revenue will we make?",
        "SELECT SUM(t.price * t.stock_quantity * (100 - COALESCE(d.pct_discount, 0)) / 100) AS revenue\n"
        "FROM t_shirts t LEFT JOIN discounts d ON d.t_shirt_id = t.t_shirt_id\n"
        "WHERE t.brand = 'Levi'",
    ),
    (
        "How much revenue would all Levi's t-shirts bring without discounts?",
        "SELECT SUM(price * stock_quantity) FROM t_shirts WHERE brand = 'Levi'",
    ),
    (
        "Which brand has the most stock?",
        "SELECT brand, SUM(stock_quantity) AS stock FROM t_shirts GROUP BY brand ORDER BY stock DESC LIMIT 1",
    ),
]


def examples_block() -> str:
    return "\n\n".join(f"Question: {q}\nSQL: {sql}" for q, sql in SQL_EXAMPLES)
