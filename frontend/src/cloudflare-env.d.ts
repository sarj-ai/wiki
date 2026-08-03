interface EdgeRateLimitBinding {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

interface CloudflareEnv {
  SMART_CREATE_RATE_LIMITER?: EdgeRateLimitBinding;
  SMART_PROPOSAL_RATE_LIMITER?: EdgeRateLimitBinding;
}
