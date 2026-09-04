import type { NextConfig } from "next";

// NO `output: "export"`. A static export has no route handlers and no
// middleware, so the Cognito gate and the decide endpoint could not exist —
// the browser would have to hold AWS credentials to read anything. Amplify
// hosts this on the WEB_COMPUTE platform for that reason.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  // **NO `serverExternalPackages` for the AWS SDK.** It was here, listing
  // `@aws-sdk/client-dynamodb` and `@aws-sdk/client-bedrock-agentcore`, on the
  // reasoning that server-only packages should not enter the client graph. That
  // reasoning is right and the mechanism is wrong: marking a package external
  // tells Next to emit a bare `require` and leave the package out of the bundle,
  // and Amplify's SSR bundle ships only what the trace includes — so the module
  // is simply absent at runtime. Measured on the deployed app:
  //
  //   ⨯ Error: Failed to load external module
  //     @aws-sdk/client-dynamodb-3e32f4e24bb075d4:
  //     Cannot find module '@aws-sdk/client-dynamodb-3e32f4e24bb075d4'
  //
  // (Turbopack appends a content hash to the external's name, which is why the
  // message names a module nobody ever published.) Every page returned 500 with
  // a *valid* session — sign-in worked, then the first DynamoDB import failed.
  //
  // Nothing was protecting the client graph anyway: `lib/cases.ts` and
  // `lib/decide.ts` are imported only from server components and a route
  // handler, so Next never traces them into a client bundle. Bundling the SDK is
  // both correct and the only thing that works here.
};

export default nextConfig;
