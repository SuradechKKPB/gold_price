import "server-only";

export interface NewsItem {
  title: string;
  url: string;
  source: string;
  date: string;
}

export interface CalEvent {
  title: string;
  date: string;
  impact: "High" | "Medium" | "Low" | string;
}

const REVALIDATE = 1800;

/** Latest Thai-language gold news via Google News RSS (free, no key). */
export async function fetchNews(): Promise<NewsItem[]> {
  try {
    const u =
      "https://news.google.com/rss/search?" +
      new URLSearchParams({ q: "ราคาทองคำ", hl: "th", gl: "TH", ceid: "TH:th" }).toString();
    const res = await fetch(u, { next: { revalidate: REVALIDATE } });
    const xml = await res.text();
    const strip = (s: string) => s.replace(/<!\[CDATA\[|\]\]>/g, "").replace(/&amp;/g, "&").trim();
    const tag = (block: string, name: string) =>
      strip((block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`)) ?? ["", ""])[1]);

    return [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)]
      .slice(0, 8)
      .map((m) => {
        const b = m[1];
        const source = tag(b, "source");
        let title = tag(b, "title");
        if (source && title.endsWith(` - ${source}`)) title = title.slice(0, -(source.length + 3));
        return { title, url: tag(b, "link"), source, date: tag(b, "pubDate") };
      })
      .filter((n) => n.title && n.url);
  } catch {
    return [];
  }
}

/** This week's high/medium-impact US events (gold-relevant) via ForexFactory calendar (free, no key). */
export async function fetchCalendar(): Promise<CalEvent[]> {
  try {
    const res = await fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json", {
      next: { revalidate: REVALIDATE },
      headers: { "User-Agent": "Mozilla/5.0" },
    });
    const data = (await res.json()) as { country: string; impact: string; title: string; date: string }[];
    return data
      .filter((e) => e.country === "USD" && (e.impact === "High" || e.impact === "Medium"))
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((e) => ({ title: e.title, date: e.date, impact: e.impact }));
  } catch {
    return [];
  }
}
