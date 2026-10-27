<script lang="ts">
  // Mounts an Observable Plot chart at the width of its container and redraws when that changes.
  // `build(width)` returns the element from Plot.plot(); nothing is drawn until the island has a measured width.
  let { build, label, minWidth = 240 }: { build: (width: number) => Element; label: string; minWidth?: number } = $props();
  let width = $state(0);
  let host: HTMLElement | undefined = $state();

  $effect(() => {
    if (!host || width < minWidth) return;
    const el = build(width);
    host.replaceChildren(el);
    return () => el.remove();
  });
</script>

<div bind:clientWidth={width} bind:this={host} role="img" aria-label={label}></div>
