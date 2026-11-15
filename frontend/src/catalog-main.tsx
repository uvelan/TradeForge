import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Alert, Box, Button, Chip, CssBaseline, FormControl, InputLabel, MenuItem, Paper, Select, Stack, TextField, ThemeProvider, Typography, createTheme } from "@mui/material";
import { CandlestickSeries, ColorType, createChart } from "lightweight-charts";
import { chartPoints, timeframes, type Dataset, type Series, type Timeframe } from "./series";
import { chartTimeOptions, TimezoneProvider, useTimezone } from "./timezone";

const theme = createTheme({
  palette: { mode: "dark", background: { default: "#101419", paper: "#171d24" }, primary: { main: "#72ddbf" } },
  typography: { fontFamily: 'Inter, "Segoe UI", sans-serif' },
  shape: { borderRadius: 8 },
});
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const failure = await response.json().catch(() => null);
    throw new Error(typeof failure?.detail === "string" ? failure.detail : `Request failed (HTTP ${response.status})`);
  }
  return response.json() as Promise<T>;
}

function Chart({ series }: { series: Series }) {
  const container = useRef<HTMLDivElement>(null);
  const { zone } = useTimezone();
  useEffect(() => {
    if (!container.current) return;
    const format = chartTimeOptions(zone);
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#171d24" }, textColor: "#aebac7", attributionLogo: true },
      grid: { vertLines: { color: "#232c35" }, horzLines: { color: "#232c35" } },
      timeScale: { timeVisible: true, tickMarkFormatter: format.tick },
      localization: { timeFormatter: format.crosshair },
    });
    const scale = series.dataset.price_scale;
    chart.addSeries(CandlestickSeries, {
      upColor: "#54d6ab", downColor: "#f07d86", wickUpColor: "#54d6ab", wickDownColor: "#f07d86", borderVisible: false,
      priceFormat: { type: "price", precision: Math.round(Math.log10(scale)), minMove: 1 / scale },
    }).setData(chartPoints(series));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, zone]);
  return <Box ref={container} role="img" aria-label={`${series.dataset.symbol} ${series.dataset.timeframe} candlestick chart`} sx={{ height: "clamp(320px, 60vh, 640px)" }} />;
}

function Terminal() {
  const { zone } = useTimezone();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [series, setSeries] = useState<Series>();
  const [timeframe, setTimeframe] = useState<Timeframe>("M15");
  const [status, setStatus] = useState("CHECKING");
  const [symbol, setSymbol] = useState("EURUSD");
  const [start, setStart] = useState(new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);

  async function open(id: string) {
    setSeries(await request<Series>(`/api/datasets/${id}`));
  }

  useEffect(() => {
    request<{ status: string }>("/api/mt5").then(data => setStatus(data.status)).catch(() => setStatus("UNAVAILABLE"));
    request<Dataset[]>("/api/datasets").then(async data => {
      setDatasets(data);
      if (data[0]) await open(data[0].id);
    }).catch(failure => setError(failure.message)).finally(() => setBusy(false));
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await action(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Request failed"); }
    finally { setBusy(false); }
  }

  async function createDataset(provider: "simulation" | "mt5", body: object) {
    const { id } = await request<{ id: string }>(`/api/datasets/${provider}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    setDatasets(await request<Dataset[]>("/api/datasets"));
    await open(id);
  }

  return <Box component="main" sx={{ maxWidth: 1440, mx: "auto", p: { xs: 2, md: 4 } }}>
    <Stack direction="row" sx={{ mb: 4, justifyContent: "space-between", alignItems: "center" }}>
      <Typography sx={{ fontWeight: 800, letterSpacing: ".04em" }}>TRADEFORGE</Typography>
      <Chip label={`MT5 ${status}`} color={status === "CONNECTED" ? "success" : "default"} variant="outlined" />
    </Stack>
    <Typography variant="h4" component="h1" sx={{ mb: 2, fontWeight: 700 }}>Market terminal</Typography>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
      <Stack direction={{ xs: "column", sm: "row" }} sx={{ mb: 3, gap: 2 }}>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel id="timeframe">Timeframe</InputLabel>
          <Select labelId="timeframe" label="Timeframe" value={timeframe} onChange={event => setTimeframe(event.target.value as Timeframe)}>
            {timeframes.map(value => <MenuItem key={value} value={value}>{value}</MenuItem>)}
          </Select>
        </FormControl>
        <Button disabled={busy} variant="contained" onClick={() => void run(() => createDataset("simulation", { timeframe }))}>Generate EURUSD simulation</Button>
        {datasets.length > 0 && <FormControl size="small" sx={{ minWidth: 230 }}>
          <InputLabel id="dataset">Saved dataset</InputLabel>
          <Select disabled={busy} labelId="dataset" label="Saved dataset" value={series?.dataset.id ?? ""} onChange={event => void run(() => open(event.target.value))}>
            {datasets.map(data => <MenuItem key={data.id} value={data.id}>{data.symbol} · {data.timeframe} · {data.provider} · {data.id.slice(0, 8)}</MenuItem>)}
          </Select>
        </FormControl>}
      </Stack>
      <Stack component="form" direction={{ xs: "column", md: "row" }} sx={{ gap: 2, mb: 3 }}
        onSubmit={event => {
          event.preventDefault();
          void run(() => createDataset("mt5", {
            symbol: symbol.trim(), timeframe, start: Date.parse(start) / 1000, end: Date.parse(end) / 1000,
          }));
        }}>
        <TextField required size="small" label="MT5 symbol" value={symbol} onChange={event => setSymbol(event.target.value)} />
        <TextField required size="small" type="date" label="From (UTC)" value={start} onChange={event => setStart(event.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
        <TextField required size="small" type="date" label="Until (UTC, exclusive)" value={end} onChange={event => setEnd(event.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
        <Button type="submit" disabled={busy || timeframes.indexOf(timeframe) > timeframes.indexOf("M30")} variant="outlined">Import MT5 candles</Button>
      </Stack>
      {series ? <>
        <Typography variant="h6" component="h2">{series.dataset.symbol} · {series.dataset.timeframe}</Typography>
        <Typography color="text.secondary" sx={{ mb: 2 }}>{series.dataset.provider} / {series.dataset.feed} · {series.dataset.row_count} candles · {zone}</Typography>
        {series.dataset.validation_status === "VERIFIED_WITH_GAPS" && <Alert severity="info" sx={{ mb: 2 }}>
          Some requested intervals have no broker candles. The dataset may include market closures or missing history.
        </Alert>}
        <Chart series={series} />
      </> : <Typography color="text.secondary" sx={{ py: 10, textAlign: "center" }}>
        {busy ? "Loading datasets…" : "Generate simulated EURUSD candles to open the interactive chart."}
      </Typography>}
    </Paper>
  </Box>;
}

createRoot(document.getElementById("root")!).render(<ThemeProvider theme={theme}><CssBaseline /><TimezoneProvider><Terminal /></TimezoneProvider></ThemeProvider>);
