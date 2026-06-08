import httpx

async def alias_dict():
    '''
        获取落雪的maimai曲目别名列表
    '''
    alias_lx_url = "https://maimai.lxns.net/api/v0/maimai/alias/list"
    aliases_dict = {}

    async with httpx.AsyncClient() as client:
        response = await client.get(alias_lx_url)
        if response.status_code == 200:
            resp_data = response.json()["aliases"]
            
            for item in resp_data:
                song_id = item["song_id"]
                for alias_name in item["aliases"]:
                    aliases_dict[alias_name] = song_id    #名字指向曲目id
        else:
            return
        
        return aliases_dict