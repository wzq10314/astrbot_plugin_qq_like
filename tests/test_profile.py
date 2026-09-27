import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_like
from qq_like_test.bot_profile import BotProfiles

class Profiles(unittest.IsolatedAsyncioTestCase):
    async def test_identity_and_cache(self):
        profile=BotProfiles()
        event=SimpleNamespace(get_self_id=lambda:'570502551',bot=SimpleNamespace(call_action=AsyncMock(return_value={'user_id':570502551,'nickname':'落落'})))
        with patch('qq_like_test.bot_profile.download_avatar',AsyncMock(return_value='data:image/jpeg;base64,test')) as avatar:
            result=await profile.get(event)
            self.assertEqual(result['bot_name'],'落落')
            avatar.assert_awaited_once_with('570502551')
            await profile.get(event)
            event.bot.call_action.assert_awaited_once_with('get_login_info')

    async def test_failure_and_wrong_identity(self):
        event=SimpleNamespace(get_self_id=lambda:'570502551',bot=SimpleNamespace(call_action=AsyncMock(return_value={'user_id':12345,'nickname':'错误账号'})))
        with patch('qq_like_test.bot_profile.download_avatar',AsyncMock(side_effect=TimeoutError)):
            result=await BotProfiles().get(event,'备用名称')
            self.assertEqual(result,{'bot_name':'备用名称','bot_avatar':''})

if __name__=='__main__':unittest.main()
