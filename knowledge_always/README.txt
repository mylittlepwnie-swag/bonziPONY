Everything in this folder is ALWAYS given to your pony — every conversation,
every autonomous thought. No searching, no lookup: it's pasted straight into
her context each time.

Each file is wrapped in a tag named after the file:

    lore.txt  ->  <lore>
                  ...what you wrote...
                  </lore>

Optional: make the first line of a file a description, and it'll be placed
after the block so she knows what it is:

    description: These are past conversations with the user of relevance.

Tips:
  - Keep it lean. This costs tokens on every single request. For big
    reference material she only needs sometimes, use the knowledge/ folder.
  - Filenames become tag names, so keep them simple (lore.txt, rules.md,
    past_convos.txt).
  - Edits take effect on the next message — no restart needed.
