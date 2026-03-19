import asyncio
import httpx

async def test_netease(artist, track):
    query = f"{artist} {track}"
    search_url = "https://music.163.com/api/search/get/web"
    params = {"s": query, "type": 1, "limit": 5}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://music.163.com/"
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.get(search_url, params=params, headers=headers)
        data = res.json()
        
        songs = data.get("result", {}).get("songs", [])
        if not songs:
            print("No songs found")
            return
            
        for i, song in enumerate(songs):
            song_id = song["id"]
            artist_name = song["artists"][0]["name"]
            song_name = song["name"]
            print(f"[{i}] {song_name} - {artist_name} (ID: {song_id})")
            
            lyric_url = "https://music.163.com/api/song/lyric"
            l_params = {"id": song_id, "lv": 1, "tv": -1, "kv": 1}
            l_res = await client.get(lyric_url, params=l_params, headers=headers)
            l_data = l_res.json()
            
            lrc = l_data.get("lrc", {}).get("lyric", "")
            print(f"  -> Lyrics length: {len(lrc)}")
            if lrc:
                print(f"  -> {lrc[:100]}...")
                break

if __name__ == "__main__":
    asyncio.run(test_netease("周杰伦", "七里香"))
