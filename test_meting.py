import asyncio
import httpx

async def test_meting(artist, track):
    query = f"{artist} {track}"
    url = "https://api.injahow.cn/meting/"
    params = {"server": "netease", "type": "search", "s": query} # or 'tencent' for QQ
    
    async with httpx.AsyncClient() as client:
        # Test netease via meting
        print("Testing Netease via Meting")
        res = await client.get(url, params=params)
        try:
            data = res.json()
            if data:
                print(f"First result: {data[0]['name']} - {data[0]['artist']}")
                lrc_url = data[0].get('lrc')
                if lrc_url:
                    print(f"LRC URL: {lrc_url}")
                    lrc_res = await client.get(lrc_url)
                    print(f"Lyrics length: {len(lrc_res.text)}")
                    print(lrc_res.text[:100])
        except Exception as e:
            print("Netease failed:", e)
            
        # Test tencent (QQ Music) via meting
        print("\nTesting Tencent via Meting")
        params["server"] = "tencent"
        res = await client.get(url, params=params)
        try:
            data = res.json()
            if data:
                print(f"First result: {data[0]['name']} - {data[0]['artist']}")
                lrc_url = data[0].get('lrc')
                if lrc_url:
                    print(f"LRC URL: {lrc_url}")
                    lrc_res = await client.get(lrc_url)
                    print(f"Lyrics length: {len(lrc_res.text)}")
                    print(lrc_res.text[:100])
        except Exception as e:
            print("Tencent failed:", e)

if __name__ == "__main__":
    asyncio.run(test_meting("周杰伦", "七里香"))
