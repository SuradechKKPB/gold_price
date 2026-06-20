import "server-only";

export interface TruthPost {
  text: string;
  date: string; // ISO
  url: string; // real Truth Social link
}

// Market/gold-relevant keywords — Trump posts move gold via Fed / tariffs / the dollar.
const KEYWORDS = [
  "federal reserve", "powell", "interest rate", "rate cut", "rate hike", "tariff",
  "dollar", "gold", "inflation", "stock market", "treasury", "oil price", "trade war",
];

const strip = (s: string) =>
  s.replace(/<!\[CDATA\[|\]\]>/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/** Latest market-relevant Donald Trump posts from Truth Social via trumpstruth.org RSS. */
export async function fetchTrumpPosts(limit = 6): Promise<TruthPost[]> {
  try {
    const res = await fetch("https://trumpstruth.org/feed", {
      next: { revalidate: 1800 },
      headers: { "User-Agent": "gold-dashboard/1.0" },
    });
    const xml = await res.text();
    const tag = (block: string, name: string) =>
      (block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`)) ?? ["", ""])[1];

    const posts: TruthPost[] = [];
    for (const m of xml.matchAll(/<item>([\s\S]*?)<\/item>/g)) {
      const b = m[1];
      let text = strip(tag(b, "title"));
      if (!text || text.startsWith("[No Title]")) text = strip(tag(b, "description"));
      if (!text) continue;
      const url = strip(tag(b, "truth:originalUrl")) || strip(tag(b, "link"));
      const date = strip(tag(b, "pubDate"));
      const hay = text.toLowerCase();
      if (!KEYWORDS.some((k) => hay.includes(k))) continue; // market-relevant only
      posts.push({ text: text.length > 240 ? text.slice(0, 237) + "…" : text, date, url });
      if (posts.length >= limit) break;
    }
    return posts;
  } catch {
    return [];
  }
}
