"use client";

import { useEffect, useRef } from "react";
import type { Point } from "@/lib/indicators";

interface Props {
  price: Point[];
  ma200: Point[];
  marker?: { time: string; text: string };
}

export default function PriceChart({ price, ma200, marker }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let cleanup = () => {};

    (async () => {
      const { createChart, ColorType, LineStyle } = await import("lightweight-charts");
      const chart = createChart(el, {
        autoSize: true,
        layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9b9286", fontFamily: "var(--font-mono)" },
        grid: { vertLines: { color: "#1c1813" }, horzLines: { color: "#1c1813" } },
        rightPriceScale: { borderColor: "#2b2620" },
        timeScale: { borderColor: "#2b2620" },
        crosshair: { mode: 0 },
      });

      const area = chart.addAreaSeries({
        lineColor: "#c9a227",
        topColor: "rgba(201,162,39,0.20)",
        bottomColor: "rgba(201,162,39,0.0)",
        lineWidth: 2,
        priceFormat: { type: "price", precision: 0, minMove: 1 },
      });
      area.setData(price as never);

      const ma = chart.addLineSeries({ color: "#6aa0d8", lineWidth: 1, lineStyle: LineStyle.Solid, priceLineVisible: false });
      ma.setData(ma200 as never);

      if (marker) {
        area.setMarkers([
          { time: marker.time as never, position: "belowBar", color: "#c8514a", shape: "arrowDown", text: marker.text },
        ]);
      }

      chart.timeScale().fitContent();
      cleanup = () => chart.remove();
    })();

    return () => cleanup();
  }, [price, ma200, marker]);

  return <div ref={ref} style={{ width: "100%", height: 360 }} />;
}
