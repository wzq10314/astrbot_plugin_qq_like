"""Bound an async command without yielding progress before useful work."""
import asyncio
import time

async def bounded_results(results, timeout=45):
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            try:
                result = await asyncio.wait_for(anext(results), remaining)
            except StopAsyncIteration:
                return
            yield result
    finally:
        await asyncio.wait_for(results.aclose(), timeout=2)
