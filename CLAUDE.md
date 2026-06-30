# Binance Spot Watchlist 自动复盘规则

请用中文输出报告，但最终必须附带一个严格 JSON 决策块。

硬规则：
- 只允许 Binance 现货，不允许合约、杠杆、借贷和做空。
- 新闻面只能增强或否决技术面信号，不能单独触发建仓或加仓。
- 未持仓标的只允许 `build` 或 `hold`；已持仓标的允许 `add`、`reduce`、`close`、`stop_loss`、`hold`。
- 每个 `build` / `add` 必须给出 `stop_loss_price`。
- 如果数据过期、新闻来源不足、价格异常或信心不足，必须输出 `hold`。
- 单币目标权重不得超过请求里的 `max_symbol_weight_pct`。
- 总现货目标权重不得超过请求里的 `max_gross_weight_pct`。

最终 JSON 使用：

```json
{
  "as_of": "ISO-8601",
  "summary": "一句话总结",
  "decisions": [
    {
      "symbol": "BTCUSDT",
      "action": "hold",
      "target_weight_pct": 0,
      "max_order_usdt": 0,
      "stop_loss_price": null,
      "confidence": 0.0,
      "technical_reason": "技术面理由",
      "fundamental_reason": "基本面理由",
      "news_reason": "新闻面理由",
      "sources": ["https://example.com"]
    }
  ]
}
```

