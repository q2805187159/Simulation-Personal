import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Graph, GraphNode } from "./types";
import "./life-graph-3d.css";

type Props = {
  graph: Graph;
  selectedId?: string;
  onSelectNode: (id: string) => void;
  className?: string;
};

type EventPoint = {
  node: GraphNode;
  point: THREE.Mesh;
  position: THREE.Vector3;
};

const CATEGORY_COLORS: Record<string, number> = {
  origin: 0xd9a441,
  education: 0x5eb7b7,
  work: 0xe07854,
  household: 0xc487b7,
  history: 0x8ba8d5,
  digital: 0x72c59b,
  health: 0xe6c160,
  life_end: 0xc95a73,
  community: 0x7fbc92,
  development: 0x79a6c9,
  finance: 0xd39e70,
  leisure: 0x9e9ed6,
  mobility: 0xd18377,
};

const categoryName: Record<string, string> = {
  origin: "出生", education: "教育", work: "工作", household: "家庭",
  history: "历史背景", digital: "数字生活", health: "健康", life_end: "生命终点",
  community: "社区", development: "发展", finance: "财务安排", leisure: "休闲", mobility: "迁移",
};

function dateYear(value: string) {
  const parsed = Number(value.slice(0, 4));
  return Number.isFinite(parsed) ? parsed : 0;
}

function colorFor(category: string) {
  return CATEGORY_COLORS[category] ?? 0xabb9a7;
}

function titleFor(node: GraphNode) {
  return `${node.title} · ${node.start.slice(0, 10)}`;
}

function eventYears(nodes: GraphNode[]) {
  const years = nodes.flatMap((node) => [dateYear(node.start), node.end ? dateYear(node.end) : dateYear(node.start)]);
  return { min: Math.min(...years, 1900), max: Math.max(...years, 1901) };
}

function laneFor(node: GraphNode, people: string[]) {
  const participant = node.participants?.[0];
  const index = participant ? people.indexOf(participant) : 0;
  return Math.max(0, index);
}

function semanticDepth(node: GraphNode, categories: string[]) {
  if (node.category === "history" || node.summary_node) return -3.2;
  const index = Math.max(0, categories.indexOf(node.category));
  const fraction = categories.length <= 1 ? 0.5 : index / (categories.length - 1);
  return -1.6 + fraction * 3.2;
}

function hashIndex(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  return hash;
}

function makeLine(points: THREE.Vector3[], material: THREE.LineBasicMaterial) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.Line(geometry, material);
}

export default function LifeGraph3D({ graph, selectedId, onSelectNode, className = "" }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const eventPointsRef = useRef<EventPoint[]>([]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [webglFailed, setWebglFailed] = useState(false);
  const [showLabels, setShowLabels] = useState(true);

  const nodes = useMemo(() => [...graph.nodes].sort((a, b) => a.start.localeCompare(b.start)), [graph.nodes]);
  const people = useMemo(() => {
    const family = graph.person.family?.map((item) => item.id) ?? [];
    return [graph.person.person_id, ...family];
  }, [graph.person.family, graph.person.person_id]);
  const domains = useMemo(
    () => [...new Set(nodes.map((node) => node.category).filter((category) => category !== "history"))].sort(),
    [nodes],
  );
  const evidenceAnchorCount = useMemo(
    () => nodes.reduce((total, node) => total + (node.evidence?.length ?? 0) + (node.dimension_updates?.length ?? 0), 0),
    [nodes],
  );
  const range = useMemo(() => eventYears(nodes), [nodes]);
  const years = useMemo(() => {
    const start = Math.floor(range.min / 10) * 10;
    const end = Math.ceil(range.max / 10) * 10;
    return Array.from({ length: Math.max(2, Math.floor((end - start) / 10) + 1) }, (_, index) => start + index * 10);
  }, [range]);
  const activeId = hoveredId ?? selectedId ?? nodes[0]?.event_id;
  const active = nodes.find((node) => node.event_id === activeId) ?? nodes[0];

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = stageRef.current;
    if (!canvas || !host) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch {
      setWebglFailed(true);
      return;
    }
    setWebglFailed(false);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.setClearColor(0x10191b, 1);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 160);
    camera.position.set(12.5, 9.5, 17);
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.minDistance = 5;
    controls.maxDistance = 34;
    controls.target.set(0, 0, 0);
    camera.lookAt(0, 0, 0);

    const world = new THREE.Group();
    scene.add(world);
    world.add(new THREE.HemisphereLight(0xbed6d0, 0x111719, 1.8));
    const keyLight = new THREE.DirectionalLight(0xffe8bd, 2.1);
    keyLight.position.set(3, 9, 8);
    world.add(keyLight);

    const min = range.min;
    const span = Math.max(1, range.max - min);
    const width = 12;
    const xFor = (year: number) => ((year - min) / span - 0.5) * width;
    const yFor = (lane: number) => (people.length - 1) * 0.75 - lane * 1.5;
    const zFor = (node: GraphNode) => node.summary_node ? -1.1 : node.category === "history" ? -0.45 : 0;

    const axisMaterial = new THREE.LineBasicMaterial({ color: 0x486366, transparent: true, opacity: 0.66 });
    const axis = makeLine([new THREE.Vector3(-width / 2, -1.6, 0), new THREE.Vector3(width / 2, -1.6, 0)], axisMaterial);
    world.add(axis);

    const gridMaterial = new THREE.LineBasicMaterial({ color: 0x26383b, transparent: true, opacity: 0.62 });
    years.forEach((year) => {
      const x = xFor(year);
      world.add(makeLine([new THREE.Vector3(x, -1.6, -1.6), new THREE.Vector3(x, 4, -1.6)], gridMaterial));
    });
    people.forEach((person, lane) => {
      const y = yFor(lane);
      world.add(makeLine([new THREE.Vector3(-width / 2, y, 0), new THREE.Vector3(width / 2, y, 0)], gridMaterial));
    });
    domains.forEach((domain, index) => {
      const z = domains.length <= 1 ? 0 : -1.6 + (index / (domains.length - 1)) * 3.2;
      world.add(makeLine(
        [new THREE.Vector3(-width / 2, -1.6, z), new THREE.Vector3(width / 2, -1.6, z)],
        new THREE.LineBasicMaterial({ color: colorFor(domain), transparent: true, opacity: 0.17 }),
      ));
    });

    const particleCount = Math.min(720, Math.max(160, nodes.length * 22));
    const particlePositions = new Float32Array(particleCount * 3);
    const particleColors = new Float32Array(particleCount * 3);
    const seed = nodes.length * 17 + range.min;
    const random = (index: number) => {
      const value = Math.sin(seed * 12.9898 + index * 78.233) * 43758.5453;
      return value - Math.floor(value);
    };
    for (let index = 0; index < particleCount; index += 1) {
      const angle = random(index) * Math.PI * 2;
      const radius = 4.2 + random(index + particleCount) * 7.5;
      particlePositions[index * 3] = Math.cos(angle) * radius;
      particlePositions[index * 3 + 1] = -2.1 + random(index + 2 * particleCount) * 7.4;
      particlePositions[index * 3 + 2] = -4.2 + random(index + 3 * particleCount) * 8.4;
      const color = new THREE.Color(0x587b78).lerp(new THREE.Color(0xd2a75c), random(index + 4 * particleCount));
      particleColors[index * 3] = color.r;
      particleColors[index * 3 + 1] = color.g;
      particleColors[index * 3 + 2] = color.b;
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
    particleGeometry.setAttribute("color", new THREE.BufferAttribute(particleColors, 3));
    const particleMaterial = new THREE.PointsMaterial({ size: 0.055, vertexColors: true, transparent: true, opacity: 0.72, sizeAttenuation: true });
    const particles = new THREE.Points(particleGeometry, particleMaterial);
    world.add(particles);

    const pointMaterials = new Map<string, THREE.MeshStandardMaterial>();
    const points: EventPoint[] = [];
    const sphereGeometry = new THREE.SphereGeometry(0.2, 18, 18);
    const evidencePositions: number[] = [];
    const evidenceColors: number[] = [];
    nodes.forEach((node) => {
      const referenceCount = (node.evidence?.length ?? 0) + (node.dimension_updates?.length ?? 0);
      const position = new THREE.Vector3(
        xFor(dateYear(node.start)),
        yFor(laneFor(node, people)),
        semanticDepth(node, domains),
      );
      const point = new THREE.Mesh(
        sphereGeometry,
        new THREE.MeshStandardMaterial({ color: colorFor(node.category), emissive: colorFor(node.category), emissiveIntensity: 0.18, roughness: 0.3, metalness: 0.15 }),
      );
      point.userData.eventId = node.event_id;
      point.position.copy(position);
      point.position.y += (hashIndex(node.event_id) % 7) * 0.075;
      point.scale.setScalar(node.summary_node ? 1 : 1.25);
      world.add(point);
      pointMaterials.set(node.event_id, point.material as THREE.MeshStandardMaterial);
      points.push({ node, point, position });

      const referenceParticles = Math.min(18, referenceCount);
      const color = new THREE.Color(node.category === "history" ? 0xe6bd6b : colorFor(node.category));
      for (let index = 0; index < referenceParticles; index += 1) {
        const seed = hashIndex(node.event_id + ":" + index);
        const angle = (index / Math.max(1, referenceParticles)) * Math.PI * 2 + (seed % 100) / 100;
        const radius = 0.29 + ((seed >>> 4) % 100) / 400;
        evidencePositions.push(
          position.x + Math.cos(angle) * radius,
          position.y + Math.sin(angle) * radius,
          position.z + (((seed >>> 11) % 100) / 100 - 0.5) * 0.28,
        );
        evidenceColors.push(color.r, color.g, color.b);
      }
    });
    eventPointsRef.current = points;
    if (evidencePositions.length) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(evidencePositions, 3));
      geometry.setAttribute("color", new THREE.Float32BufferAttribute(evidenceColors, 3));
      const material = new THREE.PointsMaterial({ size: 0.055, vertexColors: true, transparent: true, opacity: 0.92, sizeAttenuation: true });
      world.add(new THREE.Points(geometry, material));
    }

    const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x77988f, transparent: true, opacity: 0.48 });
    graph.edges.forEach((edge) => {
      const source = points.find((item) => item.node.event_id === edge.source);
      const target = points.find((item) => item.node.event_id === edge.target);
      if (!source || !target) return;
      const midpoint = source.position.clone().lerp(target.position, 0.5);
      midpoint.y += source.position.distanceTo(target.position) * 0.08;
      const curve = new THREE.QuadraticBezierCurve3(source.position, midpoint, target.position);
      world.add(makeLine(curve.getPoints(18), edgeMaterial));
    });

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const pointerMove = (event: PointerEvent) => {
      const bounds = canvas.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(points.map((item) => item.point), false)[0];
      setHoveredId(hit?.object.userData.eventId ?? null);
    };
    const click = () => {
      const hit = raycaster.intersectObjects(points.map((item) => item.point), false)[0];
      if (hit?.object.userData.eventId) onSelectNode(hit.object.userData.eventId);
    };
    canvas.addEventListener("pointermove", pointerMove);
    canvas.addEventListener("click", click);

    let stopped = false;
    let animationFrame = 0;
    const animate = () => {
      if (stopped) return;
      animationFrame = requestAnimationFrame(animate);
      points.forEach(({ node, point }) => {
        const active = node.event_id === selectedId || node.event_id === hoveredId;
        point.scale.setScalar((node.summary_node ? 0.75 : node.category === "history" ? 0.9 : 1) * (active ? 1.8 : 1));
        const material = pointMaterials.get(node.event_id);
        if (material) material.emissiveIntensity = active ? 0.85 : 0.18;
      });
      if (!reducedMotion) {
        particles.rotation.y += 0.00028;
        particles.rotation.x = Math.sin(performance.now() * 0.00008) * 0.03;
      }
      controls.update();
      renderer.render(scene, camera);
    };
    const resize = () => {
      const widthPx = host.clientWidth;
      const heightPx = Math.max(400, Math.min(640, Math.round(widthPx * 0.52)));
      renderer.setSize(widthPx, heightPx, false);
      camera.aspect = widthPx / heightPx;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    const visibility = new IntersectionObserver(([entry]) => {
      stopped = !entry.isIntersecting;
      if (!stopped) animate();
    }, { rootMargin: "200px" });
    visibility.observe(host);
    resize();
    animate();
    return () => {
      stopped = true;
      cancelAnimationFrame(animationFrame);
      visibility.disconnect();
      observer.disconnect();
      canvas.removeEventListener("pointermove", pointerMove);
      canvas.removeEventListener("click", click);
      controls.dispose();
      particleGeometry.dispose();
      particleMaterial.dispose();
      sphereGeometry.dispose();
      pointMaterials.forEach((material) => material.dispose());
      axisMaterial.dispose();
      gridMaterial.dispose();
      edgeMaterial.dispose();
      renderer.dispose();
      eventPointsRef.current = [];
    };
  }, [graph, onSelectNode, people, range, reducedMotion, nodes, years]);

  useEffect(() => {
    eventPointsRef.current.forEach(({ node, point }) => {
      const active = node.event_id === selectedId || node.event_id === hoveredId;
      point.scale.setScalar((node.summary_node ? 0.75 : node.category === "history" ? 0.9 : 1) * (active ? 1.8 : 1));
      const material = point.material as THREE.MeshStandardMaterial;
      material.emissiveIntensity = active ? 0.85 : 0.18;
    });
  }, [hoveredId, selectedId]);

  const activeNode = active;
  return (
    <section className={`life-graph-3d ${className}`} aria-labelledby="life-graph-3d-title">
      <header className="life-graph-3d-header">
        <div>
          <span className="life-graph-3d-kicker">LIFEGRAPH / 3D SPACE</span>
          <h2 id="life-graph-3d-title">三维人生宇宙</h2>
          <p>横轴为时间，纵向为参与者，深度为生活领域；历史背景独立置于后景。节点周围的点表示实际关联的来源和维度引用数量。</p>
        </div>
        <div className="life-graph-3d-controls" role="group" aria-label="三维图谱控制">
          <button type="button" aria-pressed={showLabels} onClick={() => setShowLabels((value) => !value)}>标签</button>
          <button type="button" onClick={() => onSelectNode(nodes[0]?.event_id ?? "")}>回到起点</button>
        </div>
      </header>
      <div className="life-graph-3d-stage" ref={stageRef}>
        {webglFailed ? (
          <div className="life-graph-3d-fallback" role="status">当前浏览器无法启用 WebGL，请使用下方事件清单查看完整数据。</div>
        ) : <canvas ref={canvasRef} aria-label="三维人生事件图。可拖动旋转、滚轮缩放并点击事件节点" />}
        {showLabels && !webglFailed && <div className="life-graph-3d-overlay" aria-hidden="true">
          <span>时间 X · {range.min}—{range.max}</span><span>参与者 Y · {people.length}</span><span>领域 Z · {domains.length}</span><span>来源 / 维度锚点 · {evidenceAnchorCount}</span>
        </div>}
      </div>
      <div className="life-graph-3d-readout" aria-live="polite">
        <div><span>当前节点</span><strong>{activeNode?.title ?? "请选择事件"}</strong><small>{activeNode ? titleFor(activeNode) : ""}</small></div>
        <div className="life-graph-3d-legend">{Object.entries(CATEGORY_COLORS).slice(0, 6).map(([key, color]) => <span key={key}><i style={{ backgroundColor: `#${color.toString(16).padStart(6, "0")}` }} />{categoryName[key] ?? key}</span>)}</div>
      </div>
      <details className="life-graph-3d-accessible">
        <summary>事件清单与无障碍访问</summary>
        <ol>{nodes.map((node) => <li key={node.event_id}><button type="button" onClick={() => onSelectNode(node.event_id)}>{titleFor(node)}</button><span>{categoryName[node.category] ?? node.category} · {(node.participants ?? []).length} 位参与者</span></li>)}</ol>
      </details>
    </section>
  );
}
