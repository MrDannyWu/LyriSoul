import asyncio
import httpx
import json
import re

async def test_qqmusic(artist, track):
    query = f"{artist} {track}"
    search_url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
    params = {
        "w": query,
        "format": "json",
        "p": 1,
        "n": 5
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://y.qq.com/"
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.get(search_url, params=params, headers=headers)
        
        # print first 200 chars to see what we got
        print(res.text[:200])

if __name__ == "__main__":
    asyncio.run(test_qqmusic("岛屿心情", "结束时"))
