import { describe, it, expect } from "vitest";
import { uploadDroppedMedia, kindForFile } from "./useExternalDrop";

// The Files API as the Bin uses it: browse lists folders, folder creates one,
// upload stores the file under folder_path ("" or missing means the root).
const fakeHttp = (rootFolders = []) => {
  const folders = rootFolders.map((name) => ({ name, path: name }));
  const created = [];
  const uploads = [];
  let nextId = 1;
  return {
    created,
    uploads,
    get: async (url, { params }) => {
      expect(url).toBe("/api/files/browse");
      expect(params.path).toBe("/");
      return { data: { data: { folders: [...folders] } } };
    },
    post: async (url, body) => {
      if (url === "/api/files/folder") {
        created.push(body.name);
        const folder = { name: body.name, path: body.name };
        folders.push(folder);
        return { data: { data: folder } };
      }
      expect(url).toBe("/api/files/upload");
      const file = body.get("file");
      uploads.push({ name: file.name, folder: body.get("folder_path") });
      return { data: { data: { id: nextId++, filename: file.name } } };
    },
  };
};

describe("Bin drop from the operating system", () => {
  it("uploads each file into the Files folder for its kind, not the root", async () => {
    const http = fakeHttp(["Videos"]);
    const files = [
      new File(["v"], "beach.mp4"),
      new File(["a"], "song.mp3"),
      new File(["a"], "voice.wav"),
      new File(["i"], "poster.png"),
    ];

    const docs = await uploadDroppedMedia(files, { http, apiBase: "/api" });

    expect(http.uploads).toEqual([
      { name: "beach.mp4", folder: "Videos" },
      { name: "song.mp3", folder: "Audio" },
      { name: "voice.wav", folder: "Audio" },
      { name: "poster.png", folder: "Images" },
    ]);
    expect(http.created).toEqual(["Audio", "Images"]);
    expect(docs.map((d) => d.kind)).toEqual(["video", "audio", "audio", "image"]);
  });

  it("files an unknown extension as video, like the Bin tile does", () => {
    expect(kindForFile("clip.MOV")).toBe("video");
    expect(kindForFile("notes.xyz")).toBe("video");
  });
});
