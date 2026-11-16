# Making a GitSense mod

A mod is a folder holding a `mod.json` manifest and asset files. Mods hold no code: no JavaScript and no Python.

To try a mod while you make it, install it from its folder:

```sh
gitsense --install-mod path/to/my-mod
```

- Installing copies the folder to `~/.config/gitsense/mods/<id>/`.
- After editing your folder, install it again, then reload the window.
- A mod that cannot be read is skipped with a message on the terminal. GitSense still starts.

## mod.json

Every key but `id` is optional:

```json
{
  "id": "my-mod",
  "name": "My mod",
  "version": "1.0.0",
  "description": "What it changes.",
  "themes": [{ "id": "my-dark", "name": "My Dark", "kind": "dark", "colors": { "bg": "#101418", "...": "..." } }],
  "default_themes": { "light": "my-light", "dark": "my-dark" },
  "stylesheets": ["style.css"],
  "splash": "splash.html",
  "icon": { "svg": "icon.svg", "png": "icon-512.png", "icns": "GitSense.icns" }
}
```

| Key | What it does |
|---|---|
| `id` | Lowercase letters, digits and dashes. It names the mod's folder. |
| `themes` | Added to the theme picker. They use the same color names as an exported theme, and cannot be edited, only duplicated. Ids may not clash with built-in themes or start with `custom-`. |
| `default_themes` | Applied when the mod is installed or switched on. With both, GitSense follows the system between them; with one, it uses that one. When the mod is switched off or removed, settings that pointed at its themes go back to Graphite and Daylight. |
| `stylesheets` | CSS files added to the page after GitSense's own. |
| `splash` | An HTML fragment for the loading splash. See [The splash](#the-splash). |
| `icon` | Replaces the window icon (`svg`, or `png`), the Dock icon and the GitSense.app icon (`icns`, or `png`). |

The easiest way to get theme colors is to make a theme in Settings > Themes, export it, and copy its `colors` into `mod.json`.

## The splash

The splash fragment has two parts, each between marker comments:

- Text between `<!-- splash:rider -->` and `<!-- /splash:rider -->` replaces the icon riding the commit graph.
- Text between `<!-- splash:lines -->` and `<!-- /splash:lines -->` replaces the three typed commands.
  - Give each line `<span class="sp-type sp-type-N" style="--n:LENGTH">`, where LENGTH is its number of characters.
- Scripts, event handlers and outside addresses are refused.

Animate the rider with CSS in one of the mod's `stylesheets`.

## Rules

- When several mods are on, all their themes and stylesheets apply.
- The first mod by id with a splash or an icon supplies it.
- Mod files are served only to the GitSense window, only from inside the mod's folder, and only as CSS, images, fonts or JSON.
- A mod may hold `.css`, `.svg`, `.png`, `.jpg`, `.webp`, font, `.json`, `.html` (the splash), `.icns`, `.md` and `.txt` files.
- Each file can be up to 4 MB, and the whole mod up to 16 MB.

## Sharing a mod

Zip the mod's folder and send the .zip. The other person installs it with `gitsense --install-mod`.
