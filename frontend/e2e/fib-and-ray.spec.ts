import { expect, test, type Page } from "@playwright/test";

type Drawing = { id: string; kind: string; anchors: { time: number; price: string }[]; locked: boolean };

async function installProbes(page: Page) {
  await page.addInitScript(() => {
    const texts = new WeakMap<HTMLCanvasElement, string[]>();
    const handles = new WeakMap<HTMLCanvasElement, { x: number; y: number }[]>();
    const origFillText = CanvasRenderingContext2D.prototype.fillText;
    const origClear = CanvasRenderingContext2D.prototype.clearRect;
    const origArc = CanvasRenderingContext2D.prototype.arc;
    CanvasRenderingContext2D.prototype.clearRect = function (...args: [number, number, number, number]) {
      texts.set(this.canvas, []);
      handles.set(this.canvas, []);
      return origClear.apply(this, args);
    };
    CanvasRenderingContext2D.prototype.fillText = function (text: string, ...rest: [number, number, number?]) {
      const list = texts.get(this.canvas) ?? [];
      list.push(String(text));
      texts.set(this.canvas, list);
      return origFillText.apply(this, [text, ...rest]);
    };
    CanvasRenderingContext2D.prototype.arc = function (x: number, y: number, r: number, start: number, end: number, ccw?: boolean) {
      if (r === 5) {
        const list = handles.get(this.canvas) ?? [];
        list.push({ x, y });
        handles.set(this.canvas, list);
      }
      return origArc.call(this, x, y, r, start, end, ccw);
    };
    Object.assign(window, {
      fibTextProbe: () => {
        const canvases = Array.from(document.querySelectorAll(".primary-chart canvas")) as HTMLCanvasElement[];
        return canvases.flatMap(c => texts.get(c) ?? []);
      },
      drawingHandleProbe: () => {
        const canvases = Array.from(document.querySelectorAll(".primary-chart canvas")) as HTMLCanvasElement[];
        for (const c of canvases) {
          const pts = handles.get(c);
          if (pts?.length) {
            const rect = c.getBoundingClientRect();
            return pts.map(p => ({ x: rect.left + p.x, y: rect.top + p.y }));
          }
        }
        return [];
      },
    });
  });
}

test("completes ray and Fibonacci retracement workflows with labels, drag, lock, undo/redo, persistence, and zero stale writes", async ({ page }) => {
  await installProbes(page);
  await page.addInitScript(() => {
    localStorage.setItem("tradeforge.workspace.page", "terminal");
    localStorage.setItem("tradeforge.workspace.source", "DEMO");
    localStorage.setItem("tradeforge.workspace.timeframe", "M5");
    localStorage.setItem("tradeforge.workspace.chart-comparison", "none");
  });

  const scope = encodeURIComponent("DEMO:EURUSD:M5");
  let scene: { schema_version: number; revision: number; items: Drawing[] } = {
    schema_version: 1,
    revision: 1,
    items: [
      {
        id: "00000000-0000-4000-8000-000000000099",
        kind: "horizontal",
        anchors: [{ time: 1700000000, price: "1.08500" }],
        locked: false,
        visible: true,
        label: "Existing book item",
        color: "#55d9b5",
        created_at: 1,
        updated_at: 1,
      } as any,
    ],
  };
  let writeCount = 0;
  let force409 = false;
  await page.route("**/research/api/drawings?**", async route => {
    if (!route.request().url().includes(scope)) {
      return route.fulfill({ json: { schema_version: 1, revision: 0, items: [] } });
    }
    if (route.request().method() === "POST") {
      if (force409) {
        return route.fulfill({ status: 409, json: { detail: "Drawings changed in another window." } });
      }
      writeCount++;
      const payload = route.request().postDataJSON();
      scene = { schema_version: 1, revision: scene.revision + 1, items: payload.items };
    }
    return route.fulfill({ json: scene });
  });

  await page.goto("/");
  await expect(page.getByRole("region", { name: "Primary chart" }).locator("canvas").first()).toBeVisible();
  await page.getByText(/^Drawing toolbox/).click();
  // Existing book reopened
  await expect(page.getByLabel("Select drawing").locator('option[value="00000000-0000-4000-8000-000000000099"]')).toHaveCount(1);

  // Discover Ray & Fibonacci directly on the chart toolbar
  await expect(page.getByRole("button", { name: "Draw Ray", exact: true })).toBeVisible();
  const fibBtn = page.getByRole("button", { name: "Draw Fibonacci retracement", exact: true });
  await expect(fibBtn).toBeVisible();

  // Keyboard placement of ascending Fibonacci retracement
  await fibBtn.click();
  await expect(page.getByText(/100% swing origin/)).toBeVisible();
  const plot = page.getByRole("group", { name: "Interactive market candlestick chart" });
  await plot.focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/0% swing end/)).toBeVisible();
  for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight");
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowUp");
  const firstWrite = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.keyboard.press("Enter");
  await firstWrite;

  const createdFib = scene.items.find(i => i.kind === "fib")!;
  expect(createdFib).toBeTruthy();
  const p100 = Number(createdFib.anchors[0].price);
  const p0 = Number(createdFib.anchors[1].price);
  expect(p0).toBeGreaterThan(p100); // ascending swing

  await expect.poll(async () => {
    const labels = await page.evaluate(() => (window as any).fibTextProbe() as string[]);
    return ["0%", "23.6%", "38.2%", "50%", "61.8%", "78.6%", "100%"].every(pct =>
      labels.some(line => line.startsWith(`${pct} (`)),
    );
  }).toBe(true);

  const renderedTexts = await page.evaluate(() => (window as any).fibTextProbe() as string[]);
  expect(renderedTexts.some(t => t.includes(`50% (${((p100 + p0) / 2).toFixed(5)})`))).toBe(true);

  // Direct handle drag on selected Fibonacci retracement
  await page.getByLabel("Select drawing").selectOption(createdFib.id);
  await expect.poll(async () => (await page.evaluate(() => (window as any).drawingHandleProbe())).length).toBe(2);
  const fibHandles = await page.evaluate(() => (window as any).drawingHandleProbe() as { x: number; y: number }[]);
  const beforeAnchor2 = createdFib.anchors[1];
  const dragWrite = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.mouse.move(fibHandles[1].x, fibHandles[1].y);
  await page.mouse.down();
  await page.mouse.move(fibHandles[1].x + 28, fibHandles[1].y - 18, { steps: 5 });
  await page.mouse.up();
  await dragWrite;
  expect(scene.items.find(i => i.id === createdFib.id)!.anchors[1]).not.toEqual(beforeAnchor2);

  // Cancelled placement issues zero writes
  const countBeforeCancel = writeCount;
  await fibBtn.click();
  await page.keyboard.press("Escape");
  expect(writeCount).toBe(countBeforeCancel);

  // Lock prevents Move/Delete actions
  const lockPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.getByRole("button", { name: "Lock", exact: true }).click();
  await lockPost;
  await expect(page.getByRole("button", { name: "Move drawing", exact: true })).toBeDisabled();

  // Undo & Redo
  const undoPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.getByRole("button", { name: "Undo drawing edit" }).click();
  await undoPost;
  await expect(page.getByRole("button", { name: "Move drawing", exact: true })).toBeEnabled();

  const redoPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.getByRole("button", { name: "Redo drawing edit" }).click();
  await redoPost;
  await expect(page.getByRole("button", { name: "Move drawing", exact: true })).toBeDisabled();

  // Place Ray via toolbar, drag its handle directly, and descend Fib anchors cleanly
  const unlockPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await unlockPost;

  await page.getByRole("button", { name: "Draw Ray", exact: true }).click();
  await plot.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowUp");
  const rayPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.keyboard.press("Enter");
  await rayPost;
  const createdRay = scene.items.find(i => i.kind === "ray")!;
  expect(createdRay).toBeTruthy();

  await expect.poll(async () => (await page.evaluate(() => (window as any).drawingHandleProbe())).length).toBe(2);
  const rayHandles = await page.evaluate(() => (window as any).drawingHandleProbe() as { x: number; y: number }[]);
  const rayBefore = createdRay.anchors[1];
  const rayDragPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.mouse.move(rayHandles[1].x, rayHandles[1].y);
  await page.mouse.down();
  await page.mouse.move(rayHandles[1].x + 24, rayHandles[1].y - 16, { steps: 5 });
  await page.mouse.up();
  await rayDragPost;
  expect(scene.items.find(i => i.id === createdRay.id)!.anchors[1]).not.toEqual(rayBefore);

  // Descending Fib via keyboard placement (higher anchor 1 -> lower anchor 2)
  await fibBtn.click();
  await plot.focus();
  for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight");
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowDown");
  const descPost = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/drawings?"));
  await page.keyboard.press("Enter");
  await descPost;
  const descFib = scene.items.filter(i => i.kind === "fib")[1];
  expect(Number(descFib.anchors[0].price)).toBeGreaterThan(Number(descFib.anchors[1].price));

  // Reopen after reload preserves existing book + Ray + both Fibs (toolbox remains open via workspace preference)
  await page.reload();
  await expect(page.getByRole("region", { name: "Primary chart" }).locator("canvas").first()).toBeVisible();
  await expect(page.getByLabel("Select drawing").locator("option")).toHaveCount(5);

  // Stale revision returns 409 and blocks further writes
  force409 = true;
  await page.getByLabel("Select drawing").selectOption(descFib.id);
  await page.getByRole("button", { name: "Hide", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Drawings changed");
  await expect(fibBtn).toBeDisabled();
});
