# binance-spot-daily-review

Binance 现货 watchlist 每日两次复盘缓存层。仓库只保存公开行情、技术面预筛、Codex 提示词和复盘报告，不保存 Binance API key，也不直接管理账户密钥。

## 结构

```text
.
├── config/watchlist.json       # 核心白名单 + 动态成交额 Top N
├── scripts/spot_data.py        # Binance Spot 公共行情客户端
├── scripts/build_cache.py      # 拉行情并写 cache/latest.json
├── scripts/analyze.py          # 离线技术面预筛
├── scripts/codex_review.py     # 调用 Codex CLI 并校验 JSON 决策
├── cache/                      # 行情缓存，由 binance-vps 定时更新
└── reports/                    # 预筛报告可提交；Codex 决策产物不提交
```

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_cache.py
.venv/bin/python scripts/analyze.py
.venv/bin/python scripts/codex_review.py --out reports/latest_decision.json
```

`scripts/codex_review.py` 默认使用 `codex --search exec`，会复用运行用户本机的 Codex 配置。生产环境必须把 Codex token 放在运行用户私有配置里，权限 0600，不允许提交到仓库。

## 自动更新

GitHub 托管 runner 访问 `api.binance.com` 可能返回 451 区域限制，因此定时行情更新放在 `binance-vps` 上执行，GitHub 只保存公开缓存结果。VPS 每天北京时间 09:05 和 21:05 运行：

```bash
scripts/update_cache_and_push.sh
```

并提交：

- `cache/latest.json`
- `cache/<YYYY-MM-DD>_market.json`
- `reports/latest_prefilter.json`

Codex 决策报告不提交；它属于运行时交易判断，由 `trading-system` 策略在执行前即时生成和校验。

VPS 部署示例：

```bash
sudo mkdir -p /opt
sudo chown "$USER:$USER" /opt
git clone git@github.com:wabicai/binance-spot-daily-review.git /opt/binance-spot-daily-review
cd /opt/binance-spot-daily-review
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x scripts/update_cache_and_push.sh
sudo cp deploy/binance-spot-cache-update.service /etc/systemd/system/
sudo cp deploy/binance-spot-cache-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now binance-spot-cache-update.timer
```

手动跑一次：

```bash
cd /opt/binance-spot-daily-review
./scripts/update_cache_and_push.sh
```

## 交易执行

实盘执行在 `trading-system` 的 `codex_spot_review` 策略中完成。本仓库输出的 JSON 决策只是输入，执行器还会校验权重、止损、决策时效、新闻来源和幂等状态后才允许下单。

## 风控边界

- 只允许 Binance Spot。
- 不允许合约、杠杆、借贷和做空。
- 新闻面不能单独触发建仓或加仓，只能增强或否决技术面信号。
- `build` / `add` 必须带止损价。
- 数据过期、决策非法、Codex 输出不可解析时，执行器必须不下单。
