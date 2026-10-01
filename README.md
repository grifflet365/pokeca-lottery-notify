# pokeca-lottery-notify

ポケモンカードの抽選販売情報を2時間おきに確認し、新着と締切間近をDiscordに通知する。

## 情報源

| 情報源 | 内容 |
|---|---|
| [ポケカ抽選図鑑](https://pokeca-navi.jp/lotteries/) | 個人店・Xリポスト系・一部の大手 |
| [入荷Now](https://nyuka-now.com/archives/2459) | 大手チェーン・アプリ抽選(受付中/近日開始/会員限定) |
| [ポケモンカード公式のお知らせ](https://www.pokemon-card.com/info/) | ポケモンセンターオンラインの抽選告知 |

どちらのまとめサイトも人手で更新されているため、掲載漏れや遅れはありうる。

## セットアップ

1. Discordでウェブフックを作る(サーバー設定 → 連携サービス → ウェブフック)。
2. このリポジトリの Settings → Secrets and variables → Actions に `DISCORD_WEBHOOK_URL` として登録する。
3. Actionsタブで `notify` を手動実行(Run workflow)して動作を確認する。

## 設定(config.json)

| キー | 意味 |
|---|---|
| `areas` | 抽選図鑑のうち通知する地域。`全国` はオンラインで完結する抽選 |
| `product_keywords` | 空でなければ、商品名にいずれかを含む抽選だけ通知する |
| `exclude_keywords` | 店名・商品名にいずれかを含む抽選を通知しない |
| `official_keywords` | 公式のお知らせのうち、タイトルにいずれかを含むものを通知する |
| `remind_hours_before` | 締切の何時間前に再通知するか |

## ローカルで試す

```bash
pip install -r requirements.txt
python notify.py
```

`DISCORD_WEBHOOK_URL` が無ければ送信せず、通知内容を標準出力に表示する。
`state.json` は通知済みの記録で、消すと現在掲載中のものがすべて新着として通知される。
