import { describe, it, expect } from "vitest";
import { guardHtmlMedia, unguardHtmlMedia, REMOTE_BLOCKED_PLACEHOLDER } from "../assetGuard";

// Sticky notes put saved HTML into the page with innerHTML, which loads every
// <img> at once. Remote media is swapped out before that and put back on save.
describe("guardHtmlMedia", () => {
  it("swaps a remote image for the placeholder and keeps the URL on the element", () => {
    const out = guardHtmlMedia('<p>hi</p><img src="https://cdn.example.com/a.png" alt="a">');
    expect(out).not.toMatch(/\ssrc="https:/);
    expect(out).toContain('data-remote-src="https://cdn.example.com/a.png"');
    expect(out).toContain(REMOTE_BLOCKED_PLACEHOLDER.slice(0, 20));
  });

  it("leaves local, relative and data images and plain text untouched", () => {
    const html = '<img src="/api/outputs/x.png"><img src="data:image/png;base64,AA=="> text';
    expect(guardHtmlMedia(html)).toBe(html);
    expect(guardHtmlMedia("just <b>text</b>")).toBe("just <b>text</b>");
  });

  it("covers srcset, video posters and CSS background URLs", () => {
    const out = guardHtmlMedia(
      '<img src="/l.png" srcset="https://x.example/a.png 2x">' +
        '<video poster="https://x.example/p.jpg"></video>' +
        '<div style="background-image: url(https://x.example/b.png)">b</div>',
    );
    expect(out).not.toMatch(/\s(srcset|poster)="https:/);
    expect(out).not.toMatch(/\sstyle="[^"]*url\(https:/);
    expect(out).toContain('data-remote-srcset="https://x.example/a.png 2x"');
    expect(out).toContain('data-remote-poster="https://x.example/p.jpg"');
  });

  it("round-trips: the saved note keeps its original URLs", () => {
    const html = '<p>n</p><img src="https://cdn.example.com/a.png" alt="a">';
    const back = unguardHtmlMedia(guardHtmlMedia(html));
    expect(back).toContain('src="https://cdn.example.com/a.png"');
    expect(back).not.toContain("data-remote-");
    expect(back).not.toContain("remote image blocked");
  });
});
