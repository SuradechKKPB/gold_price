import "server-only";

export interface NewsItem {
  title: string; // Thai-translated headline
  url: string; // original (English) source link
  source: string;
  date: string;
}

export interface CalEvent {
  title: string;
  date: string;
  impact: "High" | "Medium" | "Low" | string;
}

const REVALIDATE = 1800;

/** Free, keyless EN->TH translation via Google's gtx endpoint. Falls back to source text. */
async function translateToThai(text: string): Promise<string> {
  try {
    const u =
      "https://translate.googleapis.com/translate_a/single?" +
      new URLSearchParams({ client: "gtx", sl: "en", tl: "th", dt: "t", q: text }).toString();
    const res = await fetch(u, { next: { revalidate: REVALIDATE } });
    const data = (await res.json()) as [Array<[string]>];
    return data[0].map((seg) => seg[0]).join("");
  } catch {
    return text;
  }
}

/** International gold news (English sources) with Thai-translated headlines; links stay English. */
export async function fetchNews(): Promise<NewsItem[]> {
  try {
    const u =
      "https://news.google.com/rss/search?" +
      new URLSearchParams({ q: "gold price", hl: "en-US", gl: "US", ceid: "US:en" }).toString();
    const res = await fetch(u, { next: { revalidate: REVALIDATE } });
    const xml = await res.text();
    const strip = (s: string) => s.replace(/<!\[CDATA\[|\]\]>/g, "").replace(/&amp;/g, "&").trim();
    const tag = (block: string, name: string) =>
      strip((block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`)) ?? ["", ""])[1]);

    const items = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)]
      .slice(0, 8)
      .map((m) => {
        const b = m[1];
        const source = tag(b, "source");
        let title = tag(b, "title");
        if (source && title.endsWith(` - ${source}`)) title = title.slice(0, -(source.length + 3));
        return { titleEn: title, url: tag(b, "link"), source, date: tag(b, "pubDate") };
      })
      .filter((n) => n.titleEn && n.url);

    return Promise.all(
      items.map(async (n) => ({
        title: await translateToThai(n.titleEn),
        url: n.url,
        source: n.source,
        date: n.date,
      })),
    );
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
