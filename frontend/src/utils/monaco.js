// The one place the app gets Monaco from. @monaco-editor/react on its own
// fetches the editor from a public CDN on first use, so an offline machine
// never gets past "Loading..." and every visit calls a third party. This hands
// it the copy bundled from node_modules instead, with its workers.
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/language/json/json.worker?worker";
import CssWorker from "monaco-editor/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/language/html/html.worker?worker";
import TsWorker from "monaco-editor/language/typescript/ts.worker?worker";

const WORKERS = {
  json: JsonWorker,
  css: CssWorker,
  scss: CssWorker,
  less: CssWorker,
  html: HtmlWorker,
  handlebars: HtmlWorker,
  razor: HtmlWorker,
  typescript: TsWorker,
  javascript: TsWorker,
};

self.MonacoEnvironment = {
  getWorker(_workerId, label) {
    const Worker = WORKERS[label] || EditorWorker;
    return new Worker();
  },
};

loader.config({ monaco });

export { default, DiffEditor, useMonaco } from "@monaco-editor/react";
