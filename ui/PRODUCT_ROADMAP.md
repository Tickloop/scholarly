# Scholarly Canvas — Living Product Roadmap

This is a working document, not a fixed specification. It records product ideas, current decisions, open questions, and rough sequencing as the product evolves.

## Product direction

Scholarly Canvas is a visual knowledge space where people can collect entities and content, connect them, and work with an agent to understand or extend the resulting graph.

The canvas should expand beyond academic papers to represent the broader information ecosystem: content, its creators, the identities through which it is published, and the organizations around it.

## Core ontology

### Content

Canonical name: `social_media_content`.

Any media generated for social consumption. “Social media” describes how the media is made to be consumed, shared, discussed, referenced, or circulated—not merely the platform that hosts it. This is intentionally broader than the conventional use of the term and currently includes:

- arXiv and other research papers
- Instagram reels
- YouTube videos and Shorts
- Twitter/X posts
- LinkedIn posts
- Medium articles and other web articles
- songs

Possible future examples include podcasts, books, newsletters, images, and ordinary web pages.

Each content node should retain its specific subtype and source platform. A YouTube video and an arXiv paper can therefore share common content behavior without losing the fields or presentation unique to each.

### People

Entities capable of acting, creating, owning, or participating. In this product, the category intentionally includes:

- humans
- AI personas or agents
- animals
- other person-like actors

The product may eventually use a more general internal name such as `actor`, while presenting familiar labels like Person, AI, or Animal in the interface.

### Companies

Organizations or legal/on-paper entities. Companies are distinct from people even when either can create content or control accounts.

This may later broaden into an `organization` type if the graph needs to represent universities, nonprofits, governments, bands, or informal groups.

### Social media accounts

An identity on a publishing platform. An account belongs to or is operated by a person or a company. AI and animal personas are treated like people for this purpose.

Keeping accounts separate from people and companies lets the graph represent:

- one person with several accounts
- a shared company account
- a pseudonymous or not-yet-resolved account
- content published by an account while still identifying the person or company behind it

## Candidate relationships

The graph will need typed relationships rather than only a generic “related” edge. Initial candidates:

- person/company `owns` or `operates` account
- account `published` content
- person/company `created` content
- content `mentions` person/company/account/content
- content `responds_to`, `quotes`, `cites`, `remixes`, or `links_to` content
- person `works_at`, `founded`, or `represents` company

These labels are provisional. Direction, provenance, and whether a relationship was user-created or agent-inferred will matter.

## Product capabilities

### More node types

Add first-class support for content, people, companies, and accounts, with subtype-specific cards for papers, articles, videos, short-form videos, posts, songs, and future media.

The shared `social_media_content` node model should come before a large collection of one-off card components. Platform- and format-specific cards can then progressively enrich the common model.

### Persistent backend

Persist canvases, nodes, edges, positions, and relevant source metadata. Likely early needs:

- create, open, rename, and delete canvases
- autosave graph edits and viewport state
- stable node and relationship IDs
- source URLs and fetched metadata
- timestamps and refresh status
- room for agent-created content and provenance

Authentication, collaboration, version history, and sharing remain open scope questions.

### Agent chat

Add a chat interface grounded in the focused canvas. The agent should eventually be able to inspect the graph, answer questions about it, suggest or create nodes and relationships, and take canvas actions with clear user visibility.

Open interaction questions include whether chat is a side panel, a floating surface, or both, and which actions require confirmation.

### Paste a link to create a card

When a canvas is focused, pasting a URL should create a node at a sensible location, immediately show a lightweight placeholder, detect the source/type, and enrich the card with fetched metadata.

Important states: importing, ready, unsupported, failed, and stale. Duplicate URLs should be detected without preventing intentional reuse.

### Context menu

Right-clicking should expose actions appropriate to the target:

- canvas actions when clicking empty space
- node actions when clicking a node
- relationship actions when clicking an edge
- multi-selection actions when several items are selected

`Refresh/reload from source` is a core node action. Other likely actions include open source, edit, duplicate, connect, ask agent about this, change type, and remove from canvas.

### Quick action bar

Add a Cmd/Ctrl+K command palette for fast discovery and execution of product actions. It should be context-aware and share an action system with menus and agent tools so actions behave consistently regardless of where they are invoked.

Likely early commands: create node, paste/import link, search nodes, focus selection, open canvas, refresh selection, ask agent, and canvas navigation.

## Rough sequencing

This order is a starting hypothesis, not a commitment:

1. Define the shared graph ontology and action model.
2. Add local creation/editing for the foundational node types.
3. Add backend persistence and canvas lifecycle.
4. Add URL paste/import with asynchronous metadata enrichment.
5. Add the shared action system, then expose it through context menus and Cmd/Ctrl+K.
6. Add agent chat, initially read-only and then capable of proposing or performing graph actions.
7. Deepen platform-specific cards, importers, relationships, search, and collaboration.

Some user-facing work can overlap this sequence. For example, a simple command palette or context menu can arrive early, then grow as the shared action system matures.

## Principles emerging so far

- Preserve a small, coherent ontology while allowing rich content subtypes.
- Separate a creator/owner from the account used to publish.
- Keep the original source URL and source metadata.
- Make imports feel immediate, even when enrichment is asynchronous.
- Let menus, keyboard commands, and agents invoke the same underlying actions.
- Record provenance so the user can distinguish sourced facts, agent inference, and manual edits.
- Treat refresh as reconciliation with a source, not silent replacement of user edits.

## Open questions

- Are songs themselves content nodes, or should a song/recording/release have a richer media model later?
- Should a paper’s authors initially be plain metadata or automatically become connected Person nodes?
- Is a company best modeled as its own top-level type or as one subtype of Organization?
- Can an account have multiple owners/operators, and do we need time-bounded ownership?
- What is the first useful job for the agent: explaining the canvas, organizing it, researching additions, or operating it?
- Which backend and identity model fit the intended deployment and collaboration needs?

## Idea log

### 2026-09-02

- Broaden the canvas from research papers to many kinds of consumable content.
- Establish People, Companies, Accounts, and Content as the initial conceptual families.
- Treat AI and animals as person-like entities when associating them with accounts.
- Recognize arXiv papers as part of the same broad content family as posts, videos, and articles.
- Add persistent canvases and nodes through a backend.
- Add canvas-aware agent chat.
- Turn pasted links into cards.
- Add target-aware right-click menus, including refresh/reload.
- Add a Cmd/Ctrl+K quick action bar.
- Confirm `social_media_content` as the canonical umbrella: media generated for social consumption, regardless of whether it lives on a conventional social platform.
