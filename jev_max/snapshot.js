/* jev-max snapshot: pierce shadow DOM and (same-origin) iframes,
 * build one flat indexed table of actionable controls.
 *
 * Injected once per page via add_init_script. The Python driver calls
 *   window.__jev.snapshot()  -> plain JSON for the model
 *   window.__jev.node(i)     -> the live DOM node behind index i
 *
 * Indices are valid only until the next snapshot; the agent re-observes
 * after every action, so this is always fresh.
 */
(() => {
  if (window.__jev) return;

  const nodes = [];

  const MAX_NODES = 2000;
  const NAME_LEN = 80;

  const CLICKABLE_ROLES = new Set([
    "button", "link", "checkbox", "radio", "switch", "tab",
    "menuitem", "menuitemcheckbox", "menuitemradio", "option",
    "combobox", "listbox", "textbox", "searchbox", "slider",
    "spinbutton", "treeitem",
  ]);

  function truncate(s, n) {
    s = (s || "").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function labelledBy(el) {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (!ids.length) return "";
    const root = el.getRootNode();
    const parts = [];
    for (const id of ids) {
      const t = root.getElementById ? root.getElementById(id) : document.getElementById(id);
      if (t) parts.push(t.innerText || t.textContent || "");
    }
    return parts.join(" ");
  }

  function associatedLabel(el) {
    if (el.labels && el.labels.length) {
      return Array.from(el.labels).map((l) => l.innerText || "").join(" ");
    }
    if (el.id) {
      const root = el.getRootNode();
      const lab = root.querySelector ? root.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
      if (lab) return lab.innerText || "";
    }
    return "";
  }

  function accessibleName(el) {
    return (
      labelledBy(el) ||
      el.getAttribute("aria-label") ||
      associatedLabel(el) ||
      el.getAttribute("alt") ||
      el.getAttribute("title") ||
      truncate(el.innerText || el.textContent, NAME_LEN) ||
      el.getAttribute("placeholder") ||
      (el.tagName === "INPUT" && /^(submit|button|reset)$/i.test(el.type || "") ? el.value : "") ||
      ""
    );
  }

  function implicitRole(el) {
    const tag = el.tagName;
    if (tag === "A" && el.hasAttribute("href")) return "link";
    if (tag === "BUTTON") return "button";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "SELECT") return el.multiple ? "listbox" : "combobox";
    if (tag === "SUMMARY") return "button";
    if (tag === "INPUT") {
      const t = (el.type || "text").toLowerCase();
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      if (t === "submit" || t === "button" || t === "reset" || t === "image") return "button";
      if (t === "file") return "button";
      if (t === "range") return "slider";
      if (t === "number") return "spinbutton";
      if (t === "search") return "searchbox";
      if (t === "hidden") return "";
      return "textbox";
    }
    if (el.isContentEditable) return "textbox";
    return "";
  }

  function kindFor(el, role) {
    const tag = el.tagName;
    if (tag === "SELECT") return "select";
    if (tag === "INPUT" && (el.type || "").toLowerCase() === "file") return "upload";
    if (role === "checkbox" || role === "radio" || role === "switch") return "check";
    if (role === "textbox" || role === "searchbox" || role === "combobox" ||
        role === "spinbutton" || el.isContentEditable) return "fill";
    return "click";
  }

  function visible(el) {
    let r;
    try {
      r = el.getBoundingClientRect();
    } catch {
      return null;
    }
    if (!r || r.width <= 0 || r.height <= 0) return null;
    try {
      if (typeof el.checkVisibility === "function") {
        if (!el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) return null;
      } else if (getComputedStyle(el).visibility === "hidden") {
        return null;
      }
    } catch {
      return null;
    }
    if (getComputedStyle(el).pointerEvents === "none") return null;
    return r;
  }

  function inScrollableContainer(el) {
    let p = el.parentElement;
    while (p && p !== document.body && p !== document.documentElement) {
      let cs;
      try {
        cs = getComputedStyle(p);
      } catch {
        break;
      }
      const oy = cs.overflowY;
      if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight + 1) return true;
      p = p.parentElement;
    }
    return false;
  }

  const out = [];

  function pushNode(el, rect, ctx, extra) {
    if (nodes.length >= MAX_NODES) return;
    const role = el.getAttribute("role") || implicitRole(el);
    if (!role || !CLICKABLE_ROLES.has(role)) return;
    const name = truncate(accessibleName(el), NAME_LEN);
    const vw = window.innerWidth, vh = window.innerHeight;
    const offscreen =
      rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw;
    nodes.push(el);
    const id = nodes.length; // 1-based index into window.__jev.node()
    let options;
    if (el.tagName === "SELECT") {
      options = Array.from(el.options || []).slice(0, 50).map((o) => truncate(o.label || o.text, 60));
    }
    const item = {
      id,
      kind: kindFor(el, role),
      role,
      name,
      value: truncate(el.value, NAME_LEN),
      disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
      checked:
        role === "checkbox" || role === "radio" || role === "switch"
          ? !!el.checked || el.getAttribute("aria-checked") === "true"
          : undefined,
      offscreen,
      nestedScroll: inScrollableContainer(el),
      box: [
        Math.round(rect.x), Math.round(rect.y),
        Math.round(rect.width), Math.round(rect.height),
      ],
      ...ctx,
      ...(extra || {}),
    };
    if (options) item.options = options;
    if (item.checked === undefined) delete item.checked;
    out.push(item);
  }

  function walk(root, ctx) {
    let els;
    try {
      els = root.querySelectorAll("*");
    } catch {
      return;
    }
    for (const el of els) {
      if (nodes.length >= MAX_NODES) return;
      const tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" ||
          tag === "TEMPLATE" || tag === "HEAD" || tag === "HTML") {
        // still descend into shadow roots below
      } else if (tag === "IFRAME" || tag === "FRAME") {
        handleFrame(el, ctx);
        continue;
      } else {
        const rect = visible(el);
        if (rect) pushNode(el, rect, ctx);
      }
      // pierce open shadow roots
      if (el.shadowRoot) {
        walk(el.shadowRoot, { ...ctx, inShadow: true });
      }
    }
  }

  function handleFrame(el, ctx) {
    const rect = visible(el);
    if (!rect) return;
    let doc = null;
    try {
      doc = el.contentDocument; // throws or null cross-origin
    } catch {
      doc = null;
    }
    const frameCtx = {
      ...ctx,
      inFrame: truncate(el.getAttribute("title") || el.src || el.name || "frame", NAME_LEN),
    };
    if (doc && doc.documentElement) {
      walk(doc, frameCtx); // same-origin: pierce it
      return;
    }
    // cross-origin: record the region so the canvas fallback can target it
    if (nodes.length >= MAX_NODES) return;
    nodes.push(el);
    const vw = window.innerWidth, vh = window.innerHeight;
    out.push({
      id: nodes.length,
      kind: "frame",
      role: "iframe",
      name: truncate(el.getAttribute("title") || el.src || "embedded frame", NAME_LEN),
      value: "",
      disabled: false,
      crossOrigin: true,
      offscreen: rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw,
      nestedScroll: false,
      box: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
      ...ctx,
    });
  }

  function fingerprint() {
    let h = 5381;
    const s = location.href + "|" + out.map((a) => a.id + a.role + a.name).join(";");
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16);
  }

  function visibleText() {
    try {
      const t = document.body ? document.body.innerText : "";
      return truncate(t, 1500);
    } catch {
      return "";
    }
  }

  window.__jev = {
    snapshot() {
      nodes.length = 0;
      out.length = 0;
      try {
        walk(document, { inShadow: false });
      } catch (e) {
        return { error: String(e && e.message || e) };
      }
      return {
        url: location.href,
        title: document.title || "",
        fingerprint: fingerprint(),
        actions: out,
        text: visibleText(),
        truncated: nodes.length >= MAX_NODES,
        stats: {
          controls: out.length,
          shadowPierced: out.filter((a) => a.inShadow).length,
          framePierced: out.filter((a) => a.inFrame && !a.crossOrigin).length,
          crossOriginFrames: out.filter((a) => a.crossOrigin).length,
        },
      };
    },
    node(i) {
      return nodes[i - 1] || null;
    },
  };
})();
