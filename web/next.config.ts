import type { NextConfig } from "next";

// NO `output: "export"`. A static export has no route handlers and no
// middleware, so the Cognito gate and the decide endpoint could not exist —
// the browser would have to hold AWS credentials to read anything. Amplify
// hosts this on the WEB_COMPUTE platform for that reason.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Server-only packages must not be bundled into the client graph.
  serverExternalPackages: [
    "@aws-sdk/client-dynamodb",
    "@aws-sdk/client-bedrock-agentcore",
  ],
};

export default nextConfig;
