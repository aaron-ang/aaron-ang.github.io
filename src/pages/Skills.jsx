import Stack from "@mui/material/Stack";
import CardMedia from "@mui/material/CardMedia";
import { scale } from "react-animations/lib/utils";

const skills = [
  {
    title: "Front-End",
    names: ["HTML", "CSS", "JavaScript", "React"],
  },
  {
    title: "Back-End",
    names: ["SQL", "Node.js", "Flask"],
  },
  {
    title: "Other",
    names: ["Python", "Java", "Kotlin", "C++", "Git", "Matplotlib", "Numpy"],
  },
];

const Skill = ({ name }) => {
  return (
    <Stack direction="column">
      <CardMedia
        component="img"
        image={`/skills/${name}.png`}
        alt="skillIcon"
        loading="lazy"
        height="100em"
        sx={{
          transition: "all .1s ease-in-out",
          ":hover": { transform: scale(1.1) },
        }}
      />
      <p>{name}</p>
    </Stack>
  );
};

const Skills = () => {
  return (
    <Stack direction="column" justifyContent="center" height="100%">
      {skills.map((cat) => (
        <Stack
          direction="row"
          justifyContent="center"
          alignItems="center"
          spacing={3}
          margin={1}
        >
          <h1>{cat.title}</h1>
          {cat.names.map((s) => (
            <Skill name={s} />
          ))}
        </Stack>
      ))}
    </Stack>
  );
};

export default Skills;
