/**
 * "Did you know" tips, shown one at a time by components/common/DidYouKnowTip.jsx.
 *
 * Each tip describes something the interface really does today (docs/interface.md
 * has the longer version). `route` adds a "Show me" button that opens that page;
 * `action` names one of the card's built-in actions instead ("shortcuts",
 * "floating-chat"). A tip whose route the active profile hides is skipped.
 * New tips go at the end, so a browser that has seen the list meets them first.
 */
export const TIPS = Object.freeze([
  {
    id: "dashboard-card-menu",
    text: "Right-click any card on the Dashboard to open its page, minimize it, give it a colour or hide it.",
    route: "/dashboard",
  },
  {
    id: "shortcuts",
    text: "Press ? anywhere outside a text box to see the keyboard shortcuts.",
    action: "shortcuts",
  },
  {
    id: "mic-hands-free",
    text: "Click the microphone to talk hands-free: each pause sends what you said to the chat. Click it again to stop.",
  },
  {
    id: "floating-chat",
    text: "Ctrl+Shift+C opens the floating chat on any page. It knows which page you are on, so you can ask about what is in front of you.",
    action: "floating-chat",
  },
  {
    id: "drop-into-files",
    text: "Drag files or whole folders from your computer onto the Files page. They are uploaded where you drop them and indexed for chat and search.",
    route: "/documents",
  },
  {
    id: "dashboard-hidden-cards",
    text: "Hid a Dashboard card by mistake? Right-click an empty part of the Dashboard to show it again.",
    route: "/dashboard",
  },
  {
    id: "move-resize-cards",
    text: "Cards on the Dashboard, Code Editor and Video Editor move by their title bar and resize from any edge or corner. Double-click a title bar to fold a card away.",
    route: "/dashboard",
  },
  {
    id: "settings-hover-help",
    text: "In Settings, a title with a dotted underline explains itself: rest the pointer on it.",
    route: "/settings",
  },
  {
    id: "navigation-modes",
    text: "Settings → General → Navigation switches between the sidebar and a Workspaces bar that groups pages by task.",
    route: "/settings#settings-general",
  },
  {
    id: "drop-into-chat",
    text: "Drop an image onto the chat box, or paste one, to ask about it in your next message.",
    route: "/chat",
  },
  {
    id: "mic-push-to-talk",
    text: "Hold the microphone, or hold Ctrl+Shift+Space, to talk only while you hold it. Right-click the mic to change what a click does.",
  },
  {
    id: "dashboard-layouts",
    text: "Right-click an empty part of the Dashboard to cycle its layout: Normal, Compact, Compact Layered and Mode-X. Cards stay free to move in every layout.",
    route: "/dashboard",
  },
  {
    id: "footer-jobs",
    text: "While jobs run, the bar at the bottom shows them side by side. Rest the pointer on it to list every job; click to keep the list open.",
  },
  {
    id: "update-notice",
    text: "When Guaardvark is updated while it runs, a notice above the bottom bar says whether a page reload or a restart finishes the update.",
  },
  {
    id: "right-click-everywhere",
    text: "Right-click works across the app: files, notes, chat messages, jobs, rules, plugins and more each have their own menu.",
  },
]);
