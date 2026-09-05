# Repository Working Guidelines

## How to use these guidelines

- Use these guidelines as strong defaults.
- Work with the user when a different approach may be better.
- Explain the benefits and costs of a proposed change in simple terms.

## Communication

- Use simple English.
- Explain ideas directly.
- Describe what something is. Avoid lists of unrelated things that it is not.
- Use a contrast only when it clears up a real source of confusion.
- Spend tokens on useful context and clear explanations.

## Scope and delivery

- Make small changes that are easy to review.
- Break large plans into smaller pieces before starting work.
- Ask the user to choose a smaller piece when a plan covers too much work.

## UI development

- Start with the smallest functional UI built from native HTML elements.
- Add shared UI components after the basic behavior works.
- Keep shared UI primitives in `src/components/ui/`.
- Use copied ShadCN source as the base for these primitives.
- Add the project's own style and behavior to each primitive.
- Copy ShadCN component source into the project by hand.
- Use Tailwind utilities for all spacing.
- Use `react-icons` for icons.
- Use `animate-icons` for animated icons.
- Use [Animate UI](https://animate-ui.com/docs) as a source of animation ideas.

## Colors and themes

- Use black, white, and gray for most of the interface.
- Use primary and secondary brand colors where they add meaning.
- Keep the set of semantic colors small.
- Support and check dark mode for every UI change.

## Component states

- Design components that load data around four states: empty, loading, full, and error.
- Empty: show this state when the data resolves to `null`. Default component values should render a useful empty view.
- Loading: show a matching skeleton. Put the skeleton in `<Component>.skeleton.tsx`. Most components that load data should have this file.
- Full: show the complete stateful view after the data loads.
- Error: show a fallback when loading or rendering fails. Define this fallback in the component's main file.

## Network stack and efficiency

- Design the network stack with care for speed and request volume.
- Build the raw API client with Axios.
- Build React Query hooks on top of the API client.
- Use React Query to cache, deduplicate, and refresh requests.
- State each query's cache key when planning or building a hook.
- Explain which cache-key values cause a new request.
- Treat polling as an exception.
- Discuss SSE and long polling with the user before building a polling workflow.
- Choose SSE for most live data workloads.
- Use long polling when the workload or infrastructure makes SSE unsuitable.

## Testing

- Add tests only when the user asks for them.
- Write tests after the requested implementation is complete.
- Use tests as a separate step to make the implementation more solid.
- Prefer unit tests for UI components.
- Keep tests in the root-level `tests/` directory.

## Imports

- Use configured root `@` aliases for every internal project import.
- Current aliases include `@/...` and `@constants`.
