import { FiGithub, FiLinkedin, FiFileText, FiMail } from "react-icons/fi";
import Stack from "@mui/material/Stack";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

const contacts = [
  {
    title: "Github",
    href: "https://github.com/aaron-ang",
    icon: <FiGithub />,
  },
  {
    title: "LinkedIn",
    href: "https://www.linkedin.com/in/aaron-ayd/",
    icon: <FiLinkedin />,
  },
  {
    title: "Resume",
    href: "",
    icon: <FiFileText />,
  },
  {
    title: "Email",
    href: "mailto:aaron.angyd@gmail.com",
    icon: <FiMail />,
  },
];

const Contact = () => {
  return (
    <Stack spacing={2} direction="row" justifyContent="center">
      {contacts.map((c) => (
        <Tooltip title={c.title}>
          <IconButton
            aria-label={c.title.toLowerCase()}
            size="large"
            onClick={() => window.open(c.href)}
          >
            {c.icon}
          </IconButton>
        </Tooltip>
      ))}
    </Stack>
  );
};

export default Contact;
