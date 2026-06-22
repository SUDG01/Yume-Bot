import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter

#初始化 nonebot
nonebot.init()

#注册适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)


#在这里加载插件喵
#nonebot.load_plugins("thrid_party_plugins") 第三方插件
#nonebot.load_plugins("YumeBot/plugins") 本地插件
#nonebot.load_builtin_plugins("echo") #nonebot 内置插件
nonebot.load_plugins("./plugins") #加载本地插件


if __name__ == '__main__':
    nonebot.run()