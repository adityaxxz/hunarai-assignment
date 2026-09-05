import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next 16 writes AGENTS.md and CLAUDE.md on every dev start. This repo
  // keeps its agent context in notes.md, and regenerated files would be
  // committed noise.
  agentRules: false,
  /* config options here */
};

export default nextConfig;
