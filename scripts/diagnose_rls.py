#!/usr/bin/env python3
"""Diagnose whether Supabase RLS is blocking observibot_reader from seeing rows."""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

DSN = os.environ.get("SUPABASE_DB_URL")
if not DSN:
    print("ERROR: SUPABASE_DB_URL not set in environment")
    sys.exit(1)


async def main():
    import asyncpg

    print(f"Connecting to: {DSN[:40]}...")
    conn = await asyncpg.connect(DSN)

    # 1. Check current role
    role = await conn.fetchval("SELECT current_user")
    print(f"\nConnected as role: {role}")

    # 2. Check RLS on public.users
    rls = await conn.fetchrow("""
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relname = 'users'
          AND relnamespace = 'public'::regnamespace
    """)
    if rls:
        print(f"\nTable public.users:")
        print(f"  RLS enabled:  {rls['relrowsecurity']}")
        print(f"  RLS forced:   {rls['relforcerowsecurity']}")
    else:
        print("\nWARNING: public.users not found!")
        await conn.close()
        return

    # 3. Check existing policies
    policies = await conn.fetch("""
        SELECT policyname, permissive, roles, cmd, qual
        FROM pg_policies
        WHERE tablename = 'users' AND schemaname = 'public'
    """)
    print(f"\nExisting policies on public.users ({len(policies)}):")
    for p in policies:
        print(f"  - {p['policyname']}: cmd={p['cmd']}, roles={p['roles']}, "
              f"permissive={p['permissive']}, qual={p['qual']}")

    # 4. Try the actual count query
    count = await conn.fetchval("SELECT COUNT(*) FROM public.users")
    print(f"\nSELECT COUNT(*) FROM public.users = {count}")

    # 5. Check if role has BYPASSRLS
    bypass = await conn.fetchval("""
        SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user
    """)
    print(f"Role has BYPASSRLS: {bypass}")

    # 6. Check all tables with RLS enabled
    rls_tables = await conn.fetch("""
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN (
              SELECT relname FROM pg_class
              WHERE relrowsecurity = true
                AND relnamespace = 'public'::regnamespace
          )
    """)
    print(f"\nAll public tables with RLS enabled ({len(rls_tables)}):")
    for t in rls_tables:
        print(f"  - {t['schemaname']}.{t['tablename']}")

    await conn.close()

    if rls["relrowsecurity"] and count == 0:
        print("\n" + "=" * 60)
        print("DIAGNOSIS: RLS is blocking observibot_reader.")
        print("FIX: Run this SQL as a superuser:")
        print()
        for t in rls_tables:
            tname = f"{t['schemaname']}.{t['tablename']}"
            print(f"  CREATE POLICY observibot_read_{t['tablename']} "
                  f"ON {tname}")
            print(f"    FOR SELECT TO {role} USING (true);")
            print()
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
