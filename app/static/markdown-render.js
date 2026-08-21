(() => {
  "use strict";

  const SAFE_LINK_RE = /^(?:https?:\/\/|\/download\/|mailto:)/i;
  const DOWNLOAD_PATH_RE = /\/download\/[A-Za-z0-9_-]{20,100}/g;
  const TOKEN_START = "\uE000";
  const TOKEN_END = "\uE001";

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function placeholder(kind, index) {
    return `${TOKEN_START}${kind}${index}${TOKEN_END}`;
  }

  function restorePlaceholders(text, kind, values) {
    values.forEach((html, index) => {
      text = text.replace(placeholder(kind, index), html);
    });
    return text;
  }

  function renderInline(value) {
    const codeTokens = [];
    const linkTokens = [];
    let text = escapeHtml(value);

    text = text.replace(/`([^`\n]+)`/g, (_, code) => {
      const token = placeholder("C", codeTokens.length);
      codeTokens.push(`<code>${code}</code>`);
      return token;
    });

    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
      const decodedHref = href.replace(/&amp;/g, "&");
      if (!SAFE_LINK_RE.test(decodedHref)) return `${label} (${href})`;
      const token = placeholder("L", linkTokens.length);
      const download = decodedHref.startsWith("/download/") ? ' class="download-link" download' : "";
      const external = /^https?:\/\//i.test(decodedHref) ? ' target="_blank" rel="noopener noreferrer"' : "";
      linkTokens.push(`<a href="${escapeHtml(decodedHref)}"${download}${external}>${label}</a>`);
      return token;
    });

    text = text.replace(DOWNLOAD_PATH_RE, (href) => {
      const token = placeholder("L", linkTokens.length);
      linkTokens.push(`<a class="download-link" href="${escapeHtml(href)}" download>파일 다운로드</a>`);
      return token;
    });

    text = text
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>")
      .replace(/(?<!_)_([^_\n]+)_(?!_)/g, "<em>$1</em>");

    text = restorePlaceholders(text, "C", codeTokens);
    text = restorePlaceholders(text, "L", linkTokens);
    return text;
  }

  function renderMarkdown(value) {
    const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let paragraph = [];
    let listType = null;
    let inCode = false;
    let codeLanguage = "";
    let codeLines = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      output.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
      paragraph = [];
    };

    const closeList = () => {
      if (!listType) return;
      output.push(`</${listType}>`);
      listType = null;
    };

    const openList = (type) => {
      if (listType === type) return;
      closeList();
      flushParagraph();
      listType = type;
      output.push(`<${type}>`);
    };

    const flushCode = () => {
      const langClass = codeLanguage ? ` class="language-${escapeHtml(codeLanguage)}"` : "";
      output.push(`<pre><code${langClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      codeLines = [];
      codeLanguage = "";
    };

    for (const line of lines) {
      const fence = line.match(/^```\s*([\w.+-]*)\s*$/);
      if (fence) {
        if (inCode) {
          inCode = false;
          flushCode();
        } else {
          flushParagraph();
          closeList();
          inCode = true;
          codeLanguage = fence[1] || "";
        }
        continue;
      }

      if (inCode) {
        codeLines.push(line);
        continue;
      }

      if (!line.trim()) {
        flushParagraph();
        closeList();
        continue;
      }

      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        closeList();
        const level = heading[1].length;
        output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        continue;
      }

      const quote = line.match(/^>\s?(.*)$/);
      if (quote) {
        flushParagraph();
        closeList();
        output.push(`<blockquote>${renderInline(quote[1])}</blockquote>`);
        continue;
      }

      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      if (unordered) {
        openList("ul");
        output.push(`<li>${renderInline(unordered[1])}</li>`);
        continue;
      }

      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (ordered) {
        openList("ol");
        output.push(`<li>${renderInline(ordered[1])}</li>`);
        continue;
      }

      if (/^[-*_]{3,}\s*$/.test(line)) {
        flushParagraph();
        closeList();
        output.push("<hr>");
        continue;
      }

      closeList();
      paragraph.push(line);
    }

    if (inCode) flushCode();
    flushParagraph();
    closeList();
    return output.join("");
  }

  window.renderMessageText = renderMarkdown;
  window.MK4Markdown = { render: renderMarkdown };
})();
