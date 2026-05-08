import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

app.get("/", (_req, res) => {
  res.setHeader("Content-Type", "text/html");
  res.send(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Football Bot</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #0d1117; color: #e6edf3; display: flex; align-items: center;
           justify-content: center; min-height: 100vh; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 12px;
            padding: 40px 48px; text-align: center; max-width: 420px; }
    .icon { font-size: 56px; margin-bottom: 16px; }
    h1 { font-size: 24px; font-weight: 700; margin-bottom: 8px; }
    p { color: #8b949e; font-size: 15px; line-height: 1.6; margin-bottom: 24px; }
    .badge { display: inline-flex; align-items: center; gap: 8px;
             background: #1f6feb22; border: 1px solid #1f6feb66;
             color: #58a6ff; border-radius: 20px; padding: 6px 16px; font-size: 13px; }
    .dot { width: 8px; height: 8px; background: #3fb950; border-radius: 50%;
           animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⚽</div>
    <h1>Football Claude Bot</h1>
    <p>AI-powered football assistant with live scores,<br/>league subscriptions, and real-time web search.</p>
    <div class="badge"><span class="dot"></span> Bot is running</div>
  </div>
</body>
</html>`);
});

export default app;
