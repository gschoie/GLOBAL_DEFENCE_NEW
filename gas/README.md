# GAS 정본은 GS-DB2로 옮겼습니다

`youtube_defense_bot.gs`(방산 유튜브 알림 + 3일 모음)의 정본은
**[gschoie/GS-DB2 `gas/youtube_defense_bot.gs`](https://github.com/gschoie/GS-DB2/blob/main/gas/youtube_defense_bot.gs)** 입니다.

그쪽 리포에 이미 GAS 정본을 모아 두는 `gas/` 폴더가 있고(dispatch_proxy 등),
3일 모음이 GS-DB2 대시보드(`📺 ┗방산유튜브.3일모음`)로도 나가기 때문에
같은 리포에 두는 편이 맞습니다. 두 곳에 복사본을 두면 언젠가 어긋납니다.

이 리포에 남은 `youtube_digest.py`는 GitHub Actions로 같은 일을 하는 폴백
구현입니다. 실제로 도는 것은 GAS 쪽이고, 이건 쓰이지 않습니다.
