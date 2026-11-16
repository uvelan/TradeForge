import {defineConfig} from "vite";

const apiPort = Number(process.env.TRADEFORGE_E2E_API_PORT ?? "8000");
const testUI = new URL(`http://127.0.0.1:${Number(process.env.TRADEFORGE_E2E_UI_PORT ?? "4180")}`).origin;
const target = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  server:{proxy:Object.fromEntries(["/api","/research/api"].map(path=>[path,{
    target,
    configure(proxy){
      // Only the explicitly isolated development origin uses the standard local API origin.
      proxy.on("proxyReq",(outgoing,incoming)=>{
        if(incoming.headers.origin===testUI)outgoing.setHeader("origin","http://127.0.0.1:4180");
      });
    },
  }]))},
});
