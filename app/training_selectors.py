"""Safe CSS locator discovery for supervised desktop Playwright training.

Reads only DOM attributes/visible button labels, never input values or cookies.
"""
INSPECT_TARGET_JS = r"""
({x, y}) => {
  const leaf = document.elementFromPoint(x, y);
  if (!leaf) return {selector: null, label: "", tag: ""};
  const target = leaf.closest('button,[role="button"],[role="menuitem"],[role="option"],[role="radio"],a,[tabindex]') || leaf;
  const tag = target.tagName.toLowerCase();
  const edit = target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]');
  if (edit) return {selector: null, label: "Campo editável", tag};
  const attr = (node, key) => (node && node.getAttribute(key) || "").trim();
  const text = (attr(target, 'aria-label') || attr(target, 'title') ||
    attr(target.querySelector('svg[aria-label]'), 'aria-label') ||
    (['BUTTON','A'].includes(target.tagName) || target.hasAttribute('role') ? target.innerText : '') || '')
    .replace(/\\s+/g, ' ').trim().slice(0, 100);
  if (/password|senha|login|entrar|log in|código de verificação|verification code|two.factor|autentica/i.test(text)) {
    return {selector: null, label: "Autenticação", tag};
  }
  const unique = selector => {
    try { return document.querySelectorAll(selector).length === 1; } catch (_) { return false; }
  };
  const quoted = value => JSON.stringify(value);
  for (const key of ['data-testid', 'aria-label', 'title']) {
    const value = attr(target, key);
    if (value && value.length < 120) {
      const selector = tag + '[' + key + '=' + quoted(value) + ']';
      if (unique(selector)) return {selector, label: text, tag};
    }
  }
  const svgLabel = attr(target.querySelector('svg[aria-label]'), 'aria-label');
  if (svgLabel && svgLabel.length < 120) {
    const selector = tag + ':has(svg[aria-label=' + quoted(svgLabel) + '])';
    if (unique(selector)) return {selector, label: text, tag};
  }
  const parts = [];
  let node = target;
  while (node && node.nodeType === 1 && node !== document.documentElement && parts.length < 12) {
    const type = node.tagName.toLowerCase();
    const siblings = Array.from(node.parentElement ? node.parentElement.children : [])
      .filter(child => child.tagName === node.tagName);
    const index = siblings.indexOf(node) + 1;
    parts.unshift(type + ':nth-of-type(' + index + ')');
    const selector = parts.join(' > ');
    if (unique(selector)) return {selector, label: text, tag};
    node = node.parentElement;
  }
  return {selector: null, label: text, tag};
}
"""
