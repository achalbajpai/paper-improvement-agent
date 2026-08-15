import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,

  webpack: (webpackConfig) => {
    webpackConfig.watchOptions = { poll: 1000, aggregateTimeout: 300 };
    return webpackConfig;
  },
};

export default config;
