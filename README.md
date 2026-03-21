# auto_trading_system — 주식 스크리너 (수급 · 뉴스 · 실적 톤)

한국 시장(KRX)에서 **거래량 상위 종목**을 골라, 네이버 금융 **종목별 투자자 동향**(기관·외국인 순매수)을 합산하고, **Google News RSS** 헤드라인에 나오는 **실적·톤 키워드**를 단순 집계해 순위를 매기는 **연구·학습용** 도구입니다.

> **법적·투자 주의**  
> 이 프로그램은 투자 권유나 자문이 아닙니다. 데이터는 네이버·구글 등 **제3자 서비스**에서 가져오며, 누락·지연·파싱 오류가 있을 수 있습니다. 실제 매매 전에는 반드시 공시·증권사 리서치 등으로 검증하세요.

## 데이터 소스

| 구분 | 내용 |
|------|------|
| 유니버스 | `FinanceDataReader` KRX 리스트 중 KOSPI/KOSDAQ, ETF·스팩 등 제외 후 **당일 거래량 상위 N** |
| 외국인·기관 | 네이버 `item/frgn.naver` 일별 순매매량 **최근 D일 합** |
| 개인 | 전 종목 개인 순매수 공식 API는 없음. 네이버 `sise_deal_rank.naver?investor_gubun=8000`에 노출되는 **소형 랭킹**에 들어가면 플래그만 표시 |
| 뉴스·실적 톤 | Google News RSS 검색(`종목명 주식`) + 간단 한국어 키워드(호실적·적자 등) |

## 설치

```bash
cd stock-recommendation-system
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 실행

프로젝트 루트에서 `PYTHONPATH`를 현재 디렉터리로 둡니다.

```bash
cd stock-recommendation-system
PYTHONPATH=. python main.py --universe 60 --flow-days 5 --top 15
```

- `--no-news`: 뉴스 생략 (네이버만 빠르게 스캔)
- `--csv out.csv`: 결과 테이블 저장
- `--workers 4`: 동시 요청 줄이기 (차단 시)

## 매일 텔레그램 요약 (맥 슬립과 무관)

**맥이 슬립이면 그 맥에서 도는 `cron` / `launchd`는 실행되지 않습니다.** 대신 **GitHub Actions**처럼 항상 켜진 서버에서 스크립트를 돌리면, 노트북 전원·슬립과 관계없이 텔레그램으로만 받을 수 있습니다. (휴대폰은 메시지 도착 시 푸시만 받으면 되고, 본인 폰 슬립이어도 텔레그램 서버가 전달합니다.)

### 1) 봇·채팅 ID

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)에 `/newbot` 으로 봇을 만들고 **HTTP API 토큰**을 복사합니다.  
2. **본인 텔레그램 앱에서 방금 만든 봇을 검색해 `/start` 또는 아무 말이나 보냅니다.** (이걸 안 하면 봇이 나에게 메시지를 못 보낼 수 있습니다.)  
3. [@userinfobot](https://t.me/userinfobot) 에 `/start` 하면 **숫자 `Id:`** 가 나옵니다. 그게 **`TELEGRAM_CHAT_ID`** 입니다.  
4. 프로젝트 루트(`stock-recommendation-system` 폴더)에 **`.env`** 파일을 새로 만들고 두 줄만 넣습니다. (숨김 파일이라 Finder에서는 `Cmd+Shift+.` 로 보이게 할 수 있음)

   ```env
   TELEGRAM_BOT_TOKEN=BotFather가_준_토큰
   TELEGRAM_CHAT_ID=userinfobot_Id_숫자
   ```

   따옴표 없이 저장합니다.

### 2) 로컬에서 테스트

**가벼운 연결 테스트 (스크리너 실행 없음):**

```bash
cd ~/stock-recommendation-system
source .venv/bin/activate
PYTHONPATH=. python main.py --telegram-test
```

텔레그램 앱에 `🔔 연결 테스트` 메시지가 오면 설정이 맞습니다.

**실제 요약 본문까지 받아보기:**

```bash
PYTHONPATH=. python main.py --telegram --universe 40 --top 12 --no-news
```

(뉴스까지 쓰려면 `--no-news` 를 빼면 됩니다. 다만 시간이 더 걸립니다.)

### 3) GitHub에서 매일 자동 (슬립 무관)

이 저장소를 GitHub에 올린 뒤:

**Settings → Secrets and variables → Actions** 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 를 등록합니다.

`.github/workflows/daily-telegram.yml` 이 **월~금** 한국 장 시작 무렵(UTC 기준 스케줄)에 실행됩니다. 수동 실행은 Actions 탭에서 **Run workflow** 로 할 수 있습니다.

워크플로에서는 속도·안정성을 위해 `--no-news` 로 돌리도록 되어 있습니다. 뉴스까지 쓰려면 YAML에서 `--no-news` 줄을 제거하세요.

**로컬 `git push`가 거절될 때:** GitHub 토큰에 `workflow` 범위가 없으면 `.github/workflows/*.yml` 이 포함된 푸시가 막힙니다. 해결: (1) [Fine-grained / classic PAT](https://github.com/settings/tokens)에 **Workflow** 권한을 켜고 다시 푸시하거나, (2) 아래 YAML을 GitHub 웹에서 **Add file → Create new file** 로 `/.github/workflows/daily-telegram.yml` 에 붙여 넣습니다.

<details>
<summary>daily-telegram.yml 전문 (펼치기)</summary>

```yaml
# PC가 슬립이어도 GitHub 서버에서 실행되어 텔레그램만 갑니다.
# 저장소 Settings → Secrets → TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 등록 필요.

name: Daily Telegram summary

on:
  schedule:
    - cron: "10 23 * * 0-4"
  workflow_dispatch:

jobs:
  summarize:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run screener and send Telegram
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          PYTHONPATH=. python main.py \
            --telegram \
            --universe 60 \
            --flow-days 5 \
            --top 15 \
            --workers 6 \
            --no-news
```

</details>

## 한계

- KRX 공식 JSON 일부는 세션/정책으로 막히는 경우가 있어, 본 프로젝트는 **네이버 HTML 파싱**에 의존합니다. 페이지 구조가 바뀌면 수정이 필요합니다.
- 뉴스 점수는 **키워드 카운트** 수준이며, NLP나 공시(DART) 실적 서프라이즈는 포함하지 않습니다. 확장 시 DART Open API 키로 분기·연결 실적을 붙이면 좋습니다.
