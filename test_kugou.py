import asyncio
import httpx

async def test_kugou(artist, track):
    query = f"{artist} {track}"
    search_url = "http://songsearch.kugou.com/song_search_v2"
    params = {
        "keyword": query,
        "page": 1,
        "pagesize": 5,
        "platform": "WebFilter"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    async with httpx.AsyncClient() as client:
        print(f"Searching KuGou for {query}...")
        try:
            res = await client.get(search_url, params=params, headers=headers)
            data = res.json()
        except Exception as e:
            print(f"Search failed: {e}")
            return
            
        songs = data.get("data", {}).get("lists", [])
        if not songs:
            print("No songs found")
            return
            
        print("Search Results:")
        for i, song in enumerate(songs):
            filehash = song.get("FileHash")
            album_id = song.get("AlbumID")
            song_name = song.get("SongName")
            artist_name = song.get("SingerName")
            print(f"[{i}] {song_name} - {artist_name} (hash: {filehash})")
            
            # KuGou lyrics api requires getting the play endpoint first or using krc API
            # Let's try KuGou's old lyric endpoint
            l_search_url = "http://krcs.kugou.com/search"
            l_params = {
                "ver": 1,
                "man": "yes",
                "client": "mobi",
                "keyword": f"{artist_name} - {song_name}",
                "duration": song.get("Duration", 0) * 1000,
                "hash": filehash,
                "album_audio_id": song.get("ID")
            }
            try:
                l_res = await client.get(l_search_url, params=l_params, headers=headers)
                l_data = l_res.json()
                candidates = l_data.get("candidates", [])
                if not candidates:
                    continue
                    
                accesskey = candidates[0].get("accesskey")
                id = candidates[0].get("id")
                
                # Download lyric
                dl_url = "http://lyrics.kugou.com/download"
                dl_params = {
                    "ver": 1,
                    "client": "pc",
                    "id": id,
                    "accesskey": accesskey,
                    "fmt": "lrc",
                    "charset": "utf8"
                }
                dl_res = await client.get(dl_url, params=dl_params, headers=headers)
                dl_data = dl_res.json()
                lrc = dl_data.get("content", "")
                
                import base64
                if lrc:
                    lrc = base64.b64decode(lrc).decode('utf-8')
                    print(f"  -> Lyrics length: {len(lrc)}")
                    print(f"  -> {lrc[:100]}...")
                    break
            except Exception as e:
                print(f"  -> Failed to get lyrics: {e}")

if __name__ == "__main__":
    asyncio.run(test_kugou("周杰伦", "七里香"))
