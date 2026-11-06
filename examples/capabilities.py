"""Run: python -m cachescope run --threads --objects examples/capabilities.py"""

import asyncio
from concurrent.futures import ThreadPoolExecutor


class PriceBook:
    def __init__(self):
        self.unit_price = 12

    def quote(self, quantity):
        return self.unit_price * quantity


def score(value):
    total = 0
    for number in range(100_000):
        total += (number * value) % 97
    return total


def numbers(count):
    for number in range(count):
        yield number * 2


async def async_score(value):
    await asyncio.sleep(0)
    return score(value)


async def main():
    book = PriceBook()
    for _ in range(4):
        assert book.quote(3) == 36
        assert list(numbers(3)) == [0, 2, 4]
        await async_score(2)
    # Workers are created after the CLI starts tracing.
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(score, [2, 3, 2, 3]))


if __name__ == "__main__":
    asyncio.run(main())
