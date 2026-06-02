/**
 * v1.4 Batch 4 — Atelier agent cost-estimate price table.
 *
 * Prices in USD per 1M tokens. SNAPSHOT — verify against vendor pages
 * before committing to billing copy. The agent only uses these values
 * for inline cost captions on iteration rows ("≈ $0.012") and aggregate
 * tooltips on turn headers; nothing here drives accounting.
 *
 * Sources:
 *  - DashScope qwen pricing: https://help.aliyun.com/zh/model-studio/pricing
 *      (qwen-plus 2026-04 standard tier ¥0.8 input / ¥2.4 output per 1M
 *      tokens ≈ $0.11 / $0.33)
 *  - OpenAI pricing: https://openai.com/api/pricing
 *  - Anthropic pricing: https://www.anthropic.com/pricing#anthropic-api
 *
 * `unknown` model → returns 0 so the renderer falls back to "$0.000"
 * gracefully rather than crashing. Refresh quarterly.
 */
export interface ModelPrice {
    /** USD per 1M prompt (input) tokens. */
    input: number;
    /** USD per 1M completion (output) tokens. */
    output: number;
}

export const AGENT_PRICE_TABLE_V1_4: Record<string, ModelPrice> = {
    "qwen-plus": { input: 0.11, output: 0.33 },
    "qwen-max": { input: 2.8, output: 8.4 },
    "qwen-turbo": { input: 0.04, output: 0.11 },
    "gpt-4o": { input: 2.5, output: 10.0 },
    "gpt-4o-mini": { input: 0.15, output: 0.6 },
    "claude-3-5-sonnet-20241022": { input: 3.0, output: 15.0 },
    "claude-3-5-haiku-20241022": { input: 1.0, output: 5.0 },
};

/**
 * Compute a USD cost estimate for one (model, prompt_tokens,
 * completion_tokens) triplet. Returns 0 for unknown models.
 */
export function estimateCostUSD(
    model: string,
    promptTokens: number,
    completionTokens: number,
): number {
    const p = AGENT_PRICE_TABLE_V1_4[model];
    if (!p) return 0;
    return (promptTokens * p.input + completionTokens * p.output) / 1_000_000;
}

/**
 * Format a cost estimate for display. Drops to "—" when both inputs are
 * zero so the UI doesn't flash "$0.000" on iterations whose usage block
 * hasn't arrived yet.
 */
export function formatCostUSD(cost: number): string {
    if (cost <= 0) return "—";
    if (cost < 0.001) return "<$0.001";
    if (cost < 1) return `$${cost.toFixed(3)}`;
    return `$${cost.toFixed(2)}`;
}
