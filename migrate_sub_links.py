# migrate_sub_links.py
import asyncio
import asyncpg
from config import config

async def main():
    pool = await asyncpg.create_pool(config.DATABASE_URL)
    
    rows = await pool.fetch(
        "SELECT id, sub_link FROM subscriptions WHERE is_active = true"
    )
    print(f"Найдено подписок: {len(rows)}")
    
    updated = 0
    for row in rows:
        old_link = row["sub_link"]
        new_link = old_link.replace(
            "http://sub.escapethematrix.to",
            "https://vpn.escapethematrix.to:2096"
        )
        if old_link != new_link:
            await pool.execute(
                "UPDATE subscriptions SET sub_link = $1 WHERE id = $2",
                new_link, row["id"]
            )
            print(f"✅ {old_link} → {new_link}")
            updated += 1
        else:
            print(f"⏭  уже правильная: {old_link}")
    
    await pool.close()
    print(f"Готово! Обновлено: {updated}")

asyncio.run(main())