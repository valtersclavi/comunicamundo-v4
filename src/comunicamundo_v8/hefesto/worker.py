"""Hefesto V8 worker and database administration entry point."""
import argparse
import asyncio
import json
import os
from uuid import UUID
from .postgres import ProductRepository, apply_migrations


def required(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise ValueError(f'missing environment variable {name}')
    return value


async def run(args: argparse.Namespace) -> None:
    if args.command == 'v8-run':
        from .v8.runtime import run as run_v8
        result = await run_v8(args.execution_run_id)
    elif args.command == 'migrate-postgres':
        result = {'status': 'ready', 'applied': apply_migrations(
            required('HEFESTO_POSTGRES_DIRECT_URL'),
            os.environ.get('HEFESTO_MIGRATIONS_DIR', '/app/migrations'))}
    elif args.command == 'check-postgres':
        repository = ProductRepository(required('HEFESTO_POSTGRES_DIRECT_URL'))
        await repository.open()
        try:
            result = await repository.check()
        finally:
            await repository.close()
    else:
        result = {'status': 'ready', 'model_invocations': 0}
    print(json.dumps(result, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description='Hefesto V8')
    commands = root.add_subparsers(dest='command')
    v8 = commands.add_parser('v8-run')
    v8.add_argument('--execution-run-id', type=UUID, required=True)
    commands.add_parser('migrate-postgres')
    commands.add_parser('check-postgres')
    return root


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == '__main__':
    main()
